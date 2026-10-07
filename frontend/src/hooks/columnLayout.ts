import { useCallback, useRef, useSyncExternalStore } from 'react';
import type { AttributeDefinition, View } from '../api/types';

/**
 * Resizable table columns: per-browser column widths and the per-column
 * wrap / single-line toggle, kept in localStorage (never in the git-tracked
 * project data). A named view may also carry shared widths in project.json
 * (`View.column_widths`); those are the defaults and the browser's own
 * widths apply on top of them.
 *
 * One layout per table key: project + module + view (or `default` for "All
 * attributes"). The baseline page shares its module's `default` layout.
 */

/** Column key of the Id column (the backend's `VIEW_ID_COLUMN`). Attribute ids name the other columns. */
export const ID_COLUMN = '@id';
/** Width range in pixels; the same bounds the backend enforces for `View.column_widths`. */
export const MIN_COLUMN_WIDTH = 40;
export const MAX_COLUMN_WIDTH = 4000;
/** Auto-fit never makes a column wider than this (long wrapping text would otherwise span the screen). */
export const AUTO_FIT_MAX_WIDTH = 800;
export const DEFAULT_ID_COLUMN_WIDTH = 120;

export interface ColumnLayout {
  /** Column key → width in pixels (only the columns this browser resized). */
  widths: Record<string, number>;
  /** Column keys shown on a single line (truncated); every other column wraps. */
  singleLine: string[];
}

const STORAGE_PREFIX = 'reqman.columns:';
const EMPTY_LAYOUT: ColumnLayout = { widths: {}, singleLine: [] };

export function clampColumnWidth(width: number): number {
  return Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, Math.round(width)));
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
    const { widths: rawWidths, singleLine: rawSingleLine } = data as Record<string, unknown>;
    const widths: Record<string, number> = {};
    if (rawWidths && typeof rawWidths === 'object') {
      for (const [key, value] of Object.entries(rawWidths)) {
        if (typeof value === 'number' && Number.isFinite(value)) widths[key] = clampColumnWidth(value);
      }
    }
    const singleLine = Array.isArray(rawSingleLine)
      ? rawSingleLine.filter((key): key is string => typeof key === 'string')
      : [];
    return { widths, singleLine };
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
  const empty = Object.keys(layout.widths).length === 0 && layout.singleLine.length === 0;
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
  };
}

export type ResizableColumns = ReturnType<typeof useResizableColumns>;
