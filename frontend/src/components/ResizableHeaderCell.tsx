import { ActionIcon, Menu, Table } from '@mantine/core';
import { IconArrowsHorizontal, IconCheck, IconDotsVertical } from '@tabler/icons-react';
import { useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import { MAX_COLUMN_WIDTH, MIN_COLUMN_WIDTH, type ResizableColumns } from '../hooks/columnLayout';

interface ResizableHeaderCellProps {
  columnKey: string;
  label: string;
  /** Rendered width (fallback for columns without a stored width). */
  defaultWidth: number;
  columns: ResizableColumns;
  /** Extra entries of the column header menu, after a divider (e.g. Reset column widths). */
  menuExtra?: ReactNode;
}

const KEYBOARD_STEP = 10;
const KEYBOARD_STEP_LARGE = 50;

/**
 * A table header cell with a resize handle on its right edge and a small
 * column menu (also opened by right-clicking the header).
 *
 * The handle: drag to resize (pointer capture, live preview, stored on
 * release), double-click to auto-fit the content, or focus it and use
 * ArrowLeft / ArrowRight (Shift for bigger steps). Widths stay within
 * MIN_COLUMN_WIDTH..MAX_COLUMN_WIDTH, so a column never disappears.
 */
export function ResizableHeaderCell({ columnKey, label, defaultWidth, columns, menuExtra }: ResizableHeaderCellProps) {
  const width = columns.widthOf(columnKey, defaultWidth);
  const singleLine = columns.isSingleLine(columnKey);
  const [menuOpened, setMenuOpened] = useState(false);
  const [resizing, setResizing] = useState(false);
  const drag = useRef<{ pointerId: number; startX: number; startWidth: number; width: number } | null>(null);

  const finishDrag = (commit: boolean) => {
    const current = drag.current;
    drag.current = null;
    setResizing(false);
    document.body.style.removeProperty('cursor');
    document.body.style.removeProperty('user-select');
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
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const next = Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, current.startWidth + event.clientX - current.startX));
    if (next === current.width) return;
    current.width = next;
    columns.previewWidth(columnKey, next);
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
    <Table.Th
      data-column-key={columnKey}
      style={{ position: 'relative' }}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuOpened(true);
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <span className="reqman-col-label" title={label}>
          {label}
        </span>
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
            {menuExtra && (
              <>
                <Menu.Divider />
                {menuExtra}
              </>
            )}
          </Menu.Dropdown>
        </Menu>
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
