import { ActionIcon, Menu, Table } from '@mantine/core';
import { IconArrowLeft, IconArrowRight, IconArrowsHorizontal, IconCheck, IconDotsVertical } from '@tabler/icons-react';
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import { contextMenuTargetProps } from '../actions/shortcuts';
import {
  MAX_COLUMN_WIDTH,
  MIN_COLUMN_WIDTH,
  beginBodyDrag,
  endBodyDrag,
  type ColumnOrder,
  type ResizableColumns,
} from '../hooks/columnLayout';

/** `view`: the navigation/view menu (left click, ⋮ button); `edit`: the editing menu (right click, Shift+F10). */
export type ColumnMenuKind = 'view' | 'edit';

interface ResizableHeaderCellProps {
  columnKey: string;
  label: string;
  /** Rendered width (fallback for columns without a stored width). */
  defaultWidth: number;
  columns: ResizableColumns;
  /** Extra entries of the column header menu, after a divider (e.g. Reset column widths / row heights). */
  menuExtra?: ReactNode;
  /**
   * Opens the column's registry menus instead of the built-in one (module
   * page): left click on the label and the ⋮ button open the view menu,
   * right click the editing menu, at the given screen position.
   */
  onOpenMenu?: (kind: ColumnMenuKind, position: { x: number; y: number }) => void;
  /**
   * The table's reorderable columns: when it lists this column, its label can
   * be dragged left/right to move the column (the built-in menu also gets
   * Move column left/right).
   */
  order?: ColumnOrder;
}

const KEYBOARD_STEP = 10;
const KEYBOARD_STEP_LARGE = 50;
/** Pointer travel (px) before pressing a header label becomes a column drag (less: a click). */
const REORDER_THRESHOLD = 5;
/** Near the scroll container's left/right edge, a column drag scrolls the table by this much per move. */
const AUTO_SCROLL_EDGE = 40;
const AUTO_SCROLL_STEP = 20;

interface ReorderDrag {
  pointerId: number;
  startX: number;
  startY: number;
  dragging: boolean;
  /** Insertion index among the reorderable headers (before header `target`), or null. */
  target: number | null;
  th: HTMLTableCellElement;
  indicator: HTMLDivElement | null;
  onKeyDown: ((event: globalThis.KeyboardEvent) => void) | null;
}

/** The reorderable header cells of `th`'s header row, left to right. */
function reorderableHeaders(th: HTMLTableCellElement): HTMLTableCellElement[] {
  const row = th.parentElement;
  return row ? Array.from(row.querySelectorAll<HTMLTableCellElement>(':scope > th[data-column-reorderable]')) : [];
}

/**
 * A table header cell with a resize handle on its right edge and a small
 * column menu (also opened by right-clicking the header).
 *
 * The handle: drag to resize (pointer capture, live preview, stored on
 * release), double-click to auto-fit the content, or focus it and use
 * ArrowLeft / ArrowRight (Shift for bigger steps). Widths stay within
 * MIN_COLUMN_WIDTH..MAX_COLUMN_WIDTH, so a column never disappears.
 *
 * With `order`, the label can be dragged (pointer events) to reorder the
 * column: a vertical indicator shows where it lands, release drops it, Esc
 * cancels. A press without movement stays a click (the label's menu).
 */
export function ResizableHeaderCell({
  columnKey,
  label,
  defaultWidth,
  columns,
  menuExtra,
  onOpenMenu,
  order,
}: ResizableHeaderCellProps) {
  const width = columns.widthOf(columnKey, defaultWidth);
  const singleLine = columns.isSingleLine(columnKey);
  const [menuOpened, setMenuOpened] = useState(false);
  const [resizing, setResizing] = useState(false);
  const drag = useRef<{ pointerId: number; startX: number; startWidth: number; width: number } | null>(null);
  const reorder = useRef<ReorderDrag | null>(null);
  // A finished column drag ends with a click on the label: do not treat it as one.
  const suppressClick = useRef(false);
  const orderIndex = order ? order.keys.indexOf(columnKey) : -1;
  const reorderable = order !== undefined && orderIndex >= 0;

  // Unmounted mid-drag (view switch, live-sync re-render): give the body its cursor and text selection back.
  useEffect(
    () => () => {
      if (drag.current) endBodyDrag();
      if (reorder.current) cleanUpReorder(reorder.current);
    },
    [],
  );

  const moveBy = (delta: number) => {
    if (order && reorderable) order.move(columnKey, orderIndex + delta);
  };

  const finishDrag = (commit: boolean) => {
    const current = drag.current;
    drag.current = null;
    setResizing(false);
    endBodyDrag();
    if (!current) return;
    if (commit && current.width !== current.startWidth) columns.setWidth(columnKey, current.width);
    else columns.previewWidth(columnKey, null);
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: width, width };
    setResizing(true);
    // Keep the resize cursor and avoid selecting text while the pointer is outside the handle.
    beginBodyDrag('col-resize');
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const next = Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, current.startWidth + event.clientX - current.startX));
    if (next === current.width) return;
    current.width = next;
    columns.previewWidth(columnKey, next);
  };

  const onLabelPointerDown = (event: PointerEvent<HTMLSpanElement>) => {
    suppressClick.current = false;
    if (!reorderable || event.button !== 0) return;
    const th = event.currentTarget.closest('th');
    if (!th) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    reorder.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      dragging: false,
      target: null,
      th,
      indicator: null,
      onKeyDown: null,
    };
  };

  const finishReorder = (commit: boolean) => {
    const current = reorder.current;
    reorder.current = null;
    if (!current) return;
    cleanUpReorder(current);
    if (!current.dragging) return;
    suppressClick.current = true;
    if (!commit || current.target === null || !order || !reorderable) return;
    // The insertion index counts this column itself; without it, later positions shift by one.
    const to = current.target > orderIndex ? current.target - 1 : current.target;
    if (to !== orderIndex) order.move(columnKey, to);
  };

  const onLabelPointerMove = (event: PointerEvent<HTMLSpanElement>) => {
    const current = reorder.current;
    if (!current || current.pointerId !== event.pointerId) return;
    if (!current.dragging) {
      if (Math.hypot(event.clientX - current.startX, event.clientY - current.startY) < REORDER_THRESHOLD) return;
      current.dragging = true;
      beginBodyDrag('grabbing');
      current.th.dataset.columnDragging = '';
      const indicator = document.createElement('div');
      indicator.className = 'reqman-col-drop-indicator';
      document.body.appendChild(indicator);
      current.indicator = indicator;
      const span = event.currentTarget;
      const pointerId = event.pointerId;
      current.onKeyDown = (keyEvent) => {
        if (keyEvent.key !== 'Escape') return;
        keyEvent.preventDefault();
        keyEvent.stopPropagation();
        finishReorder(false);
        if (span.hasPointerCapture(pointerId)) span.releasePointerCapture(pointerId);
      };
      window.addEventListener('keydown', current.onKeyDown, true);
    }
    const scroller = current.th.closest<HTMLElement>('.reqman-table-scroll');
    if (scroller) {
      const box = scroller.getBoundingClientRect();
      if (event.clientX < box.left + AUTO_SCROLL_EDGE) scroller.scrollLeft -= AUTO_SCROLL_STEP;
      else if (event.clientX > box.right - AUTO_SCROLL_EDGE) scroller.scrollLeft += AUTO_SCROLL_STEP;
    }
    const headers = reorderableHeaders(current.th);
    if (headers.length === 0 || !current.indicator) return;
    const rects = headers.map((header) => header.getBoundingClientRect());
    const target = rects.filter((rect) => rect.left + rect.width / 2 < event.clientX).length;
    current.target = target;
    const x = target < rects.length ? rects[target].left : rects[rects.length - 1].right;
    const top = current.th.getBoundingClientRect().top;
    const table = current.th.closest('table')?.getBoundingClientRect();
    const bottom = Math.min(table?.bottom ?? window.innerHeight, scroller?.getBoundingClientRect().bottom ?? window.innerHeight);
    const style = current.indicator.style;
    style.left = `${x}px`;
    style.top = `${top}px`;
    style.height = `${Math.max(0, bottom - top)}px`;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? KEYBOARD_STEP_LARGE : KEYBOARD_STEP;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      event.stopPropagation();
      columns.setWidth(columnKey, width + (event.key === 'ArrowLeft' ? -step : step));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      columns.autoFit(columnKey);
    }
  };

  return (
    // No inline `position`: the sticky header's `th` is `position: sticky`, which also anchors the
    // absolutely placed handle (index.css makes a non-sticky header's `th` relative).
    <Table.Th
      data-column-key={columnKey}
      data-column-reorderable={reorderable || undefined}
      {...(onOpenMenu ? contextMenuTargetProps : {})}
      onContextMenu={(event) => {
        event.preventDefault();
        if (onOpenMenu) onOpenMenu('edit', { x: event.clientX, y: event.clientY });
        else setMenuOpened(true);
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <span
          className="reqman-col-label"
          title={reorderable ? `${label} (drag to move the column)` : label}
          data-reorderable={reorderable || undefined}
          style={onOpenMenu && !reorderable ? { cursor: 'pointer' } : undefined}
          onPointerDown={onLabelPointerDown}
          onPointerMove={onLabelPointerMove}
          onPointerUp={() => finishReorder(true)}
          onPointerCancel={() => finishReorder(false)}
          onLostPointerCapture={() => {
            if (reorder.current) finishReorder(true);
          }}
          onClick={(event) => {
            if (suppressClick.current) {
              suppressClick.current = false;
              return;
            }
            onOpenMenu?.('view', { x: event.clientX, y: event.clientY });
          }}
        >
          {label}
        </span>
        {onOpenMenu ? (
          <ActionIcon
            variant="subtle"
            color="gray"
            size="xs"
            aria-label={`Column menu: ${label}`}
            aria-haspopup="menu"
            className="reqman-col-menu-button"
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              onOpenMenu('view', { x: rect.left, y: rect.bottom });
            }}
          >
            <IconDotsVertical size={12} />
          </ActionIcon>
        ) : (
          <Menu opened={menuOpened} onChange={setMenuOpened} position="bottom-end" shadow="md" withinPortal>
            <Menu.Target>
              <ActionIcon
                variant="subtle"
                color="gray"
                size="xs"
                aria-label={`Column menu: ${label}`}
                className="reqman-col-menu-button"
              >
                <IconDotsVertical size={12} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>{label}</Menu.Label>
              <Menu.Item
                leftSection={
                  singleLine ? <span style={{ display: 'inline-block', width: 14 }} /> : <IconCheck size={14} />
                }
                onClick={() => columns.toggleSingleLine(columnKey)}
              >
                Wrap text
              </Menu.Item>
              <Menu.Item leftSection={<IconArrowsHorizontal size={14} />} onClick={() => columns.autoFit(columnKey)}>
                Auto-fit width
              </Menu.Item>
              {reorderable && (
                <>
                  <Menu.Item
                    leftSection={<IconArrowLeft size={14} />}
                    disabled={orderIndex === 0}
                    onClick={() => moveBy(-1)}
                  >
                    Move column left
                  </Menu.Item>
                  <Menu.Item
                    leftSection={<IconArrowRight size={14} />}
                    disabled={orderIndex === order.keys.length - 1}
                    onClick={() => moveBy(1)}
                  >
                    Move column right
                  </Menu.Item>
                </>
              )}
              {menuExtra && (
                <>
                  <Menu.Divider />
                  {menuExtra}
                </>
              )}
            </Menu.Dropdown>
          </Menu>
        )}
      </div>
      <div
        className="reqman-col-resize-handle"
        data-column-fit-ignore=""
        data-resizing={resizing || undefined}
        role="separator"
        aria-orientation="vertical"
        aria-label={`Resize column ${label}`}
        aria-valuenow={width}
        aria-valuemin={MIN_COLUMN_WIDTH}
        aria-valuemax={MAX_COLUMN_WIDTH}
        tabIndex={0}
        title="Drag to resize, double-click to fit the content"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={() => finishDrag(true)}
        onPointerCancel={() => finishDrag(false)}
        onLostPointerCapture={() => {
          if (drag.current) finishDrag(true);
        }}
        onDoubleClick={(event) => {
          event.stopPropagation();
          columns.autoFit(columnKey);
        }}
        onKeyDown={onKeyDown}
        onClick={(event) => event.stopPropagation()}
      />
    </Table.Th>
  );
}

/** Ends a column drag's side effects: the drop indicator, the dragged look, the body cursor and the Esc listener. */
function cleanUpReorder(drag: ReorderDrag): void {
  drag.indicator?.remove();
  delete drag.th.dataset.columnDragging;
  if (drag.onKeyDown) window.removeEventListener('keydown', drag.onKeyDown, true);
  if (drag.dragging) endBodyDrag();
}
