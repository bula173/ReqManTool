import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import {
  MAX_ROW_HEIGHT,
  MIN_ROW_HEIGHT,
  beginBodyDrag,
  endBodyDrag,
  minRowHeight,
  type ResizableRows,
} from '../hooks/columnLayout';

interface RowResizeHandleProps {
  rowId: string;
  rows: ResizableRows;
  /**
   * The keyboard-accessible handle of the row (one per row, e.g. in the Id
   * cell). The handles of the other cells are pointer-only.
   */
  focusable?: boolean;
}

const KEYBOARD_STEP = 5;
const KEYBOARD_STEP_LARGE = 25;

/**
 * The resize handle on a table cell's bottom edge; every cell of a row has
 * one, so the whole bottom border of the row resizes it (Excel-like).
 *
 * Drag to resize the row (pointer capture, live preview, stored on release),
 * double-click to fit the row to its content again (drops the fixed height),
 * or focus the row's focusable handle and use ArrowUp / ArrowDown (Shift for
 * bigger steps) or Enter (fit). A row never gets smaller than one line.
 *
 * The handle is a sibling of the cell content, so double-clicking the cell
 * itself still starts inline editing. The click that ends a drag is stopped so
 * a resize does not touch the row selection; a plain click (no movement)
 * reaches the row and selects it.
 */
export function RowResizeHandle({ rowId, rows, focusable = false }: RowResizeHandleProps) {
  const [resizing, setResizing] = useState(false);
  const drag = useRef<{
    pointerId: number;
    row: HTMLTableRowElement;
    startY: number;
    startHeight: number;
    minHeight: number;
    height: number;
  } | null>(null);
  // Whether the last pointer drag changed the height (its trailing click must not select the row).
  const dragMoved = useRef(false);

  // Unmounted mid-drag (row collapsed or deleted by live sync): give the body its cursor and text selection back.
  useEffect(
    () => () => {
      if (drag.current) endBodyDrag();
    },
    [],
  );

  const finishDrag = (commit: boolean) => {
    const current = drag.current;
    drag.current = null;
    setResizing(false);
    endBodyDrag();
    if (!current) return;
    dragMoved.current = current.height !== current.startHeight;
    if (commit && current.height !== current.startHeight) rows.setHeight(rowId, current.height);
    else rows.previewHeight(current.row, rowId, null);
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const row = event.currentTarget.closest('tr');
    if (!row) return;
    event.preventDefault();
    event.stopPropagation();
    dragMoved.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
    const startHeight = Math.round(row.getBoundingClientRect().height);
    drag.current = {
      pointerId: event.pointerId,
      row,
      startY: event.clientY,
      startHeight,
      minHeight: minRowHeight(row),
      height: startHeight,
    };
    setResizing(true);
    // Keep the resize cursor and avoid selecting text while the pointer is outside the handle.
    beginBodyDrag('row-resize');
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const next = Math.round(
      Math.min(MAX_ROW_HEIGHT, Math.max(current.minHeight, current.startHeight + event.clientY - current.startY)),
    );
    if (next === current.height) return;
    current.height = next;
    rows.previewHeight(current.row, rowId, next);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const row = event.currentTarget.closest('tr');
    if (!row) return;
    // Leave modified keys to the app shortcuts (e.g. Alt+ArrowUp moves the requirement).
    if (event.altKey || event.metaKey || event.ctrlKey) return;
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault();
      event.stopPropagation();
      const step = event.shiftKey ? KEYBOARD_STEP_LARGE : KEYBOARD_STEP;
      const height = rows.heightOf(rowId) ?? Math.round(row.getBoundingClientRect().height);
      const next = height + (event.key === 'ArrowUp' ? -step : step);
      rows.setHeight(rowId, Math.min(MAX_ROW_HEIGHT, Math.max(minRowHeight(row), next)));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      event.stopPropagation();
      rows.autoFit(rowId);
    }
  };

  return (
    <div
      className="reqman-row-resize-handle"
      data-column-fit-ignore=""
      data-resizing={resizing || undefined}
      title="Drag to resize the row, double-click to fit the content"
      {...(focusable
        ? {
            role: 'separator',
            'aria-orientation': 'horizontal' as const,
            'aria-label': `Resize row ${rowId}`,
            'aria-valuenow': rows.heightOf(rowId),
            'aria-valuemin': MIN_ROW_HEIGHT,
            'aria-valuemax': MAX_ROW_HEIGHT,
            tabIndex: 0,
            onKeyDown,
          }
        : { 'aria-hidden': true })}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={() => finishDrag(true)}
      onPointerCancel={() => finishDrag(false)}
      onLostPointerCapture={() => {
        if (drag.current) finishDrag(true);
      }}
      onDoubleClick={(event) => {
        event.stopPropagation();
        rows.autoFit(rowId);
      }}
      onClick={(event) => {
        if (!dragMoved.current) return;
        dragMoved.current = false;
        event.stopPropagation();
      }}
    />
  );
}
