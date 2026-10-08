import { useCallback, useLayoutEffect, useRef, useSyncExternalStore, type CSSProperties } from 'react';
import type { AttributeDefinition, View } from '../api/types';

/**
 * Resizable table columns and rows: per-browser column widths, the
 * per-column wrap / single-line toggle and per-requirement row heights, kept
 * in localStorage (never in the git-tracked project data). A named view may also carry shared widths in project.json
 * (`View.column_widths`); those are the defaults and the browser's own
 * widths apply on top of them.
 *
 * One layout per table key: project + module + view (or `default` for "All
 * attributes"). It also holds this browser's order of the attribute
 * columns. The baseline page shares its module's `default` layout.
 */

/** Column key of the Id column (the backend's `VIEW_ID_COLUMN`). Attribute ids name the other columns. */
export const ID_COLUMN = '@id';
/**
 * Column key of the computed, read-only Verification status column of a
 * requirements module (not an attribute: never stored, never in a view's
 * stored attributes or widths; its width is a per-browser preference).
 */
export const VERIFICATION_COLUMN = '@verification';
export const DEFAULT_VERIFICATION_COLUMN_WIDTH = 150;
/** Per browser: whether requirements tables show the Verification status column. */
export const VERIFICATION_COLUMN_STORAGE_KEY = 'reqman.verificationColumn';
/** Width range in pixels; the same bounds the backend enforces for `View.column_widths`. */
export const MIN_COLUMN_WIDTH = 40;
export const MAX_COLUMN_WIDTH = 4000;
/** Auto-fit never makes a column wider than this (long wrapping text would otherwise span the screen). */
export const AUTO_FIT_MAX_WIDTH = 800;
export const DEFAULT_ID_COLUMN_WIDTH = 120;
/** Row height range in pixels. The drag also keeps at least one line of content (see `minRowHeight`). */
export const MIN_ROW_HEIGHT = 24;
export const MAX_ROW_HEIGHT = 2000;
/**
 * The height of the tallest first line of a row's cells (the 28px Actions
 * icons; the Id's md text and the empty-value dash are ~25px), used for the
 * minimum row height. The CSS variable `--reqman-row-min-line` on
 * `.reqman-resizable-table` (index.css) carries the same value; this is the
 * fallback when it is not set.
 */
const ONE_LINE_HEIGHT = 28;
/** The CSS custom property with the height of one line of cell content (see `ONE_LINE_HEIGHT`). */
const ONE_LINE_VAR = '--reqman-row-min-line';

export interface ColumnLayout {
  /** Column key → width in pixels (only the columns this browser resized). */
  widths: Record<string, number>;
  /** Column keys shown on a single line (truncated); every other column wraps. */
  singleLine: string[];
  /** Requirement id → fixed row height in pixels (only the rows this browser resized); other rows fit their content. */
  rowHeights: Record<string, number>;
  /**
   * Attribute column keys in the order this browser shows them (dragged
   * headers, Move column left/right); empty: the natural order. Columns not
   * listed (e.g. attributes added later) follow the listed ones.
   */
  order: string[];
}

const STORAGE_PREFIX = 'reqman.columns:';
const EMPTY_LAYOUT: ColumnLayout = { widths: {}, singleLine: [], rowHeights: {}, order: [] };

export function clampColumnWidth(width: number): number {
  return Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, Math.round(width)));
}

export function clampRowHeight(height: number): number {
  return Math.min(MAX_ROW_HEIGHT, Math.max(MIN_ROW_HEIGHT, Math.round(height)));
}

/** The localStorage key of a module's requirements table (per view). */
export function moduleTableKey(projectId: string, moduleId: string, viewId: string | null): string {
  return `${STORAGE_PREFIX}module/${projectId}/${moduleId}/${viewId ?? 'default'}`;
}

/** A column's width when neither this browser nor the view sets one. */
export function defaultColumnWidth(attr: AttributeDefinition): number {
  if (attr.system) return 220;
  switch (attr.type) {
    case 'text':
      return 360;
    case 'integer':
    case 'boolean':
      return 110;
    case 'enum':
    case 'date':
      return 140;
    default:
      return 200;
  }
}

function parseLayout(raw: string | null): ColumnLayout {
  if (!raw) return EMPTY_LAYOUT;
  try {
    const data: unknown = JSON.parse(raw);
    if (!data || typeof data !== 'object') return EMPTY_LAYOUT;
    const {
      widths: rawWidths,
      singleLine: rawSingleLine,
      rowHeights: rawRowHeights,
      order: rawOrder,
    } = data as Record<string, unknown>;
    const widths: Record<string, number> = {};
    if (rawWidths && typeof rawWidths === 'object') {
      for (const [key, value] of Object.entries(rawWidths)) {
        if (typeof value === 'number' && Number.isFinite(value)) widths[key] = clampColumnWidth(value);
      }
    }
    const singleLine = Array.isArray(rawSingleLine)
      ? rawSingleLine.filter((key): key is string => typeof key === 'string')
      : [];
    // Older entries have no `rowHeights`: every row fits its content.
    const rowHeights: Record<string, number> = {};
    if (rawRowHeights && typeof rawRowHeights === 'object') {
      for (const [key, value] of Object.entries(rawRowHeights)) {
        if (typeof value === 'number' && Number.isFinite(value)) rowHeights[key] = clampRowHeight(value);
      }
    }
    // Older entries have no `order`: the natural column order.
    const order = Array.isArray(rawOrder) ? rawOrder.filter((key): key is string => typeof key === 'string') : [];
    return { widths, singleLine, rowHeights, order };
  } catch {
    return EMPTY_LAYOUT;
  }
}

function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

// Parsed layouts by key: stable snapshots for useSyncExternalStore.
const cache = new Map<string, ColumnLayout>();
const listeners = new Set<() => void>();

export function readColumnLayout(key: string): ColumnLayout {
  let layout = cache.get(key);
  if (!layout) {
    layout = parseLayout(browserStorage()?.getItem(key) ?? null);
    cache.set(key, layout);
  }
  return layout;
}

function writeColumnLayout(key: string, layout: ColumnLayout): void {
  const empty =
    Object.keys(layout.widths).length === 0 &&
    layout.singleLine.length === 0 &&
    Object.keys(layout.rowHeights).length === 0 &&
    layout.order.length === 0;
  cache.set(key, empty ? EMPTY_LAYOUT : layout);
  try {
    if (empty) browserStorage()?.removeItem(key);
    else browserStorage()?.setItem(key, JSON.stringify(layout));
  } catch {
    // Storage full or unavailable: the layout still applies for this session.
  }
  for (const listener of listeners) listener();
}

export function updateColumnLayout(key: string, update: (layout: ColumnLayout) => ColumnLayout): void {
  writeColumnLayout(key, update(readColumnLayout(key)));
}

/** Drops this browser's widths for a table (its wrap toggles stay); the view's or the default widths apply again. */
export function resetColumnWidths(key: string): void {
  updateColumnLayout(key, (layout) => ({ ...layout, widths: {} }));
}

export function hasColumnWidthOverrides(key: string): boolean {
  return Object.keys(readColumnLayout(key).widths).length > 0;
}

/** Drops this browser's column order for a table: the natural order applies again. */
export function resetColumnOrder(key: string): void {
  updateColumnLayout(key, (layout) => ({ ...layout, order: [] }));
}

export function hasColumnOrderOverride(key: string): boolean {
  return readColumnLayout(key).order.length > 0;
}

/**
 * `items` in a stored column order: the listed keys first, in that order,
 * then the unlisted ones in their natural order.
 */
export function applyColumnOrder<T>(items: T[], keyOf: (item: T) => string, order: string[]): T[] {
  if (order.length === 0) return items;
  const rank = new Map(order.map((key, index) => [key, index]));
  return items
    .map((item, index) => ({ item, index, rank: rank.get(keyOf(item)) }))
    .sort((a, b) => {
      if (a.rank !== undefined && b.rank !== undefined) return a.rank - b.rank;
      if (a.rank !== undefined) return -1;
      if (b.rank !== undefined) return 1;
      return a.index - b.index;
    })
    .map(({ item }) => item);
}

/** The reorderable columns of a table as shown (`keys`) and a way to move one of them. */
export interface ColumnOrder {
  keys: string[];
  /** Moves `key` to position `toIndex` of `keys` (clamped); stores the new order. */
  move: (key: string, toIndex: number) => void;
}

/** Drops this browser's row heights for a table: every row fits its content again. */
export function resetRowHeights(key: string): void {
  updateColumnLayout(key, (layout) => ({ ...layout, rowHeights: {} }));
}

export function hasRowHeightOverrides(key: string): boolean {
  return Object.keys(readColumnLayout(key).rowHeights).length > 0;
}

/**
 * The widths to store in a view: every column of the view (Id first, then
 * its attributes), as this browser currently shows them.
 */
export function viewColumnWidths(
  key: string,
  view: View,
  attributes: AttributeDefinition[],
): Record<string, number> {
  const layout = readColumnLayout(key);
  const widthOf = (columnKey: string, fallback: number) =>
    clampColumnWidth(layout.widths[columnKey] ?? view.column_widths?.[columnKey] ?? fallback);
  const widths: Record<string, number> = { [ID_COLUMN]: widthOf(ID_COLUMN, DEFAULT_ID_COLUMN_WIDTH) };
  for (const attr of attributes) {
    if (view.attribute_ids.includes(attr.id)) widths[attr.id] = widthOf(attr.id, defaultColumnWidth(attr));
  }
  return widths;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab changed a layout: re-read it.
  const onStorage = (event: StorageEvent) => {
    if (event.key === null) cache.clear();
    else if (event.key.startsWith(STORAGE_PREFIX)) cache.delete(event.key);
    else return;
    listener();
  };
  window.addEventListener('storage', onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener('storage', onStorage);
  };
}

export function useColumnLayout(key: string): ColumnLayout {
  return useSyncExternalStore(subscribe, () => readColumnLayout(key));
}

/**
 * Measures the natural (single-line, unwrapped) width of a column's content:
 * every cell marked `data-column-key={key}` in `table`, header included.
 * Each cell's content is cloned into an invisible max-content box inside the
 * cell itself, so it keeps the cell's font; all boxes are measured in one
 * layout pass.
 */
function measureColumn(table: HTMLTableElement, key: string): number | null {
  const cells = Array.from(table.querySelectorAll<HTMLElement>(`[data-column-key="${CSS.escape(key)}"]`));
  if (cells.length === 0) return null;
  const probes = cells.map((cell) => {
    const probe = document.createElement('div');
    probe.style.cssText =
      'position:absolute;left:0;top:0;width:max-content;visibility:hidden;pointer-events:none;';
    for (const child of Array.from(cell.childNodes)) {
      if (child instanceof HTMLElement && child.dataset.columnFitIgnore !== undefined) continue;
      probe.appendChild(child.cloneNode(true));
    }
    cell.appendChild(probe);
    return probe;
  });
  let widest = 0;
  probes.forEach((probe, index) => {
    const style = getComputedStyle(cells[index]);
    const chrome =
      parseFloat(style.paddingLeft) + parseFloat(style.paddingRight) +
      parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth);
    widest = Math.max(widest, probe.getBoundingClientRect().width + (Number.isFinite(chrome) ? chrome : 0));
  });
  for (const probe of probes) probe.remove();
  return Math.ceil(widest) + 2;
}

/**
 * A resizable table's column state: effective widths (browser override, then
 * the view's shared width, then the column default), the single-line toggle,
 * live drag preview and auto-fit.
 *
 * Render a `<colgroup>` whose `<col>`s get `colRef(key)` and `width:
 * widthOf(key, default)`, give the table `tableRef` with `table-layout:
 * fixed`, and mark every cell of a column with `data-column-key` (for
 * auto-fit).
 */
export function useResizableColumns(storageKey: string, viewWidths?: Record<string, number> | null) {
  const layout = useColumnLayout(storageKey);
  const tableElement = useRef<HTMLTableElement | null>(null);
  const colRefs = useRef(new Map<string, HTMLTableColElement>());
  // The rendered col width and table min-width while a drag previews a new width.
  const previewBase = useRef<{ key: string; colWidth: number; tableMinWidth: number } | null>(null);

  const widthOf = (key: string, fallback: number): number =>
    clampColumnWidth(layout.widths[key] ?? viewWidths?.[key] ?? fallback);

  // Callback refs (not ref objects), so render code can read the rest of the returned state.
  const tableRef = useCallback((el: HTMLTableElement | null) => {
    tableElement.current = el;
  }, []);

  const colRef = useCallback(
    (key: string) => (el: HTMLTableColElement | null) => {
      if (el) colRefs.current.set(key, el);
      else colRefs.current.delete(key);
    },
    [],
  );

  /** Shows `width` for `key` without re-rendering (during a drag); null restores the rendered width. */
  const previewWidth = (key: string, width: number | null) => {
    const col = colRefs.current.get(key);
    const table = tableElement.current;
    if (!col || !table) return;
    if (!previewBase.current || previewBase.current.key !== key) {
      previewBase.current = {
        key,
        colWidth: parseFloat(col.style.width) || col.getBoundingClientRect().width,
        tableMinWidth: parseFloat(table.style.minWidth) || 0,
      };
    }
    const base = previewBase.current;
    const next = width === null ? base.colWidth : clampColumnWidth(width);
    col.style.width = `${next}px`;
    if (base.tableMinWidth) table.style.minWidth = `${base.tableMinWidth + next - base.colWidth}px`;
    if (width === null) previewBase.current = null;
  };

  const setWidth = (key: string, width: number) => {
    previewBase.current = null;
    updateColumnLayout(storageKey, (current) => ({
      ...current,
      widths: { ...current.widths, [key]: clampColumnWidth(width) },
    }));
  };

  const autoFit = (key: string) => {
    const table = tableElement.current;
    if (!table) return;
    const natural = measureColumn(table, key);
    if (natural !== null) setWidth(key, Math.min(natural, AUTO_FIT_MAX_WIDTH));
  };

  const isSingleLine = (key: string) => layout.singleLine.includes(key);

  /** `items` (the reorderable columns, in their natural order) in this browser's column order. */
  const ordered = <T,>(items: T[], keyOf: (item: T) => string): T[] => applyColumnOrder(items, keyOf, layout.order);

  /** The order controls of the reorderable columns shown as `keys` (already ordered by `ordered`). */
  const orderOf = (keys: string[]): ColumnOrder => ({
    keys,
    move: (key, toIndex) => {
      const from = keys.indexOf(key);
      if (from < 0) return;
      const rest = keys.filter((k) => k !== key);
      const to = Math.min(rest.length, Math.max(0, toIndex));
      if (to === from) return;
      rest.splice(to, 0, key);
      // Keep the positions of stored keys that are not shown now (e.g. another view's columns) after these.
      updateColumnLayout(storageKey, (current) => ({
        ...current,
        order: [...rest, ...current.order.filter((k) => !rest.includes(k))],
      }));
    },
  });

  const toggleSingleLine = (key: string) =>
    updateColumnLayout(storageKey, (current) => ({
      ...current,
      singleLine: current.singleLine.includes(key)
        ? current.singleLine.filter((k) => k !== key)
        : [...current.singleLine, key],
    }));

  return {
    tableRef,
    colRef,
    widthOf,
    previewWidth,
    setWidth,
    autoFit,
    isSingleLine,
    toggleSingleLine,
    resetWidths: () => resetColumnWidths(storageKey),
    hasWidthOverrides: Object.keys(layout.widths).length > 0,
    ordered,
    orderOf,
    resetOrder: () => resetColumnOrder(storageKey),
    hasOrderOverride: layout.order.length > 0,
  };
}

export type ResizableColumns = ReturnType<typeof useResizableColumns>;

/**
 * While a resize handle (or a column header, to reorder it) is dragged, the
 * body keeps the drag cursor and text selection is off (the pointer may leave the handle). Every drag must call
 * `endBodyDrag` when it ends, including when its handle unmounts mid-drag.
 */
export function beginBodyDrag(cursor: 'col-resize' | 'row-resize' | 'grabbing'): void {
  document.body.style.cursor = cursor;
  document.body.style.userSelect = 'none';
}

export function endBodyDrag(): void {
  document.body.style.removeProperty('cursor');
  document.body.style.removeProperty('user-select');
}

/** The CSS custom property that carries a row's fixed height (see `.reqman-resizable-table` in index.css). */
const ROW_HEIGHT_VAR = '--reqman-row-height';
/** Marks the cell content wrapper whose content is clipped by a fixed row height. */
const CELL_CLIP_SELECTOR = '.reqman-cell-clip';

/** The smallest height of `row` that still shows one line of content: the cell padding plus one line. */
export function minRowHeight(row: HTMLTableRowElement): number {
  const cell = row.cells[0];
  if (!cell) return MIN_ROW_HEIGHT;
  const style = getComputedStyle(cell);
  const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
  const line = parseFloat(style.getPropertyValue(ONE_LINE_VAR));
  return clampRowHeight(
    (Number.isFinite(padding) ? padding : 0) + (Number.isFinite(line) ? line : ONE_LINE_HEIGHT) + 1,
  );
}

/**
 * Flags the clipped cells of a fixed-height row (`data-overflowing`), so only
 * those show the fade. A cell cut off by less than one line of its text (e.g.
 * taller one-line content at the minimum height) is not faded.
 */
function markOverflow(row: HTMLTableRowElement): void {
  for (const clip of Array.from(row.querySelectorAll<HTMLElement>(CELL_CLIP_SELECTOR))) {
    const hidden = clip.scrollHeight - clip.clientHeight;
    let overflowing = row.dataset.rowHeight !== undefined && hidden > 1;
    if (overflowing) {
      const lineHeight = parseFloat(getComputedStyle(clip).lineHeight);
      overflowing = hidden >= (Number.isFinite(lineHeight) ? lineHeight : ONE_LINE_HEIGHT);
    }
    if (overflowing) clip.dataset.overflowing = '';
    else delete clip.dataset.overflowing;
  }
}

/**
 * A resizable table's row heights (per requirement id, stored with the
 * column layout): a fixed height, or none (the row fits its content).
 *
 * Give the `<tbody>` `tbodyRef`; give each resizable `<tr>`
 * `data-row-height={heightOf(id)}` and `style={rowStyle(id)}`; wrap each
 * cell's content in `<div className="reqman-cell-clip">` (with `data-fade`
 * in wrapping columns), and put a `RowResizeHandle` in each cell.
 */
export function useResizableRows(storageKey: string) {
  const layout = useColumnLayout(storageKey);
  const tbodyElement = useRef<HTMLTableSectionElement | null>(null);
  // The rendered height of the row a drag previews (null: the row fits its content).
  const previewBase = useRef<{ id: string; height: string | null } | null>(null);

  const tbodyRef = useCallback((el: HTMLTableSectionElement | null) => {
    tbodyElement.current = el;
  }, []);

  // After every render (content, widths or heights may have changed): refresh the fade flags of the fixed rows.
  useLayoutEffect(() => {
    const tbody = tbodyElement.current;
    if (!tbody) return;
    for (const row of Array.from(tbody.querySelectorAll<HTMLTableRowElement>(':scope > tr[data-row-height]'))) {
      markOverflow(row);
    }
  });

  const heightOf = (id: string): number | undefined => layout.rowHeights[id];

  const rowStyle = (id: string): CSSProperties => {
    const height = layout.rowHeights[id];
    return height === undefined ? {} : ({ [ROW_HEIGHT_VAR]: `${height}px` } as CSSProperties);
  };

  /** Shows `height` on `row` without re-rendering (during a drag); null restores the rendered height. */
  const previewHeight = (row: HTMLTableRowElement, id: string, height: number | null) => {
    if (!previewBase.current || previewBase.current.id !== id) {
      previewBase.current = { id, height: row.dataset.rowHeight ?? null };
    }
    const next = height === null ? previewBase.current.height : String(clampRowHeight(height));
    if (next === null) {
      delete row.dataset.rowHeight;
      row.style.removeProperty(ROW_HEIGHT_VAR);
    } else {
      row.dataset.rowHeight = next;
      row.style.setProperty(ROW_HEIGHT_VAR, `${next}px`);
    }
    markOverflow(row);
    if (height === null) previewBase.current = null;
  };

  const setHeight = (id: string, height: number) => {
    previewBase.current = null;
    updateColumnLayout(storageKey, (current) => ({
      ...current,
      rowHeights: { ...current.rowHeights, [id]: clampRowHeight(height) },
    }));
  };

  /** Fits the row to its content again: drops its fixed height. */
  const autoFit = (id: string) => {
    previewBase.current = null;
    if (readColumnLayout(storageKey).rowHeights[id] === undefined) return;
    updateColumnLayout(storageKey, (current) => {
      const rowHeights = { ...current.rowHeights };
      delete rowHeights[id];
      return { ...current, rowHeights };
    });
  };

  return {
    tbodyRef,
    heightOf,
    rowStyle,
    previewHeight,
    setHeight,
    autoFit,
    resetHeights: () => resetRowHeights(storageKey),
    hasHeightOverrides: Object.keys(layout.rowHeights).length > 0,
  };
}

export type ResizableRows = ReturnType<typeof useResizableRows>;
