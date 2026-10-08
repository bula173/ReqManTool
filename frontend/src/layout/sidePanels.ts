import { useLocalStorage, useViewportSize } from '@mantine/hooks';
import { useState } from 'react';
import { getAction } from '../actions/registry';
import { formatShortcut } from '../actions/shortcuts';

/**
 * The collapsible, resizable side panels: the left panel (project/module
 * tree, `Sidebar`) and the right panel (requirement detail, module page).
 *
 * Open/collapsed state and widths are kept per browser in localStorage, never
 * in the git-tracked project data. On narrow screens (`NARROW_LAYOUT_QUERY`)
 * both panels start collapsed and open as overlays over the page; the stored
 * desktop state is left untouched there (see `AppActionsProvider`).
 */
export type SidePanel = 'left' | 'right';

/** Mantine's `sm` breakpoint: below it the panels are overlays. */
export const NARROW_LAYOUT_QUERY = '(max-width: 48em)';

/** The width of a collapsed panel's rail (with its expand chevron). */
export const SIDE_PANEL_RAIL_WIDTH = 28;

export const LEFT_PANEL_OPEN_STORAGE_KEY = 'reqman.leftPanel.open';
/** Kept from before the panels were collapsible, so a hidden detail panel stays hidden. */
export const DETAIL_PANEL_OPEN_STORAGE_KEY = 'reqman.detailPanelOpen';

const WIDTH_STORAGE_KEYS: Record<SidePanel, string> = {
  left: 'reqman.leftPanel.width',
  right: 'reqman.detailPanel.width',
};

interface WidthLimits {
  min: number;
  max: number;
  default: number;
  /** At most this fraction of the window width (but never below `min`), so the table keeps room. */
  maxViewportFraction: number;
}

export const SIDE_PANEL_WIDTHS: Record<SidePanel, WidthLimits> = {
  left: { min: 200, max: 640, default: 280, maxViewportFraction: 0.4 },
  right: { min: 260, max: 760, default: 320, maxViewportFraction: 0.45 },
};

/** Arrow-key steps of a focused resize handle (Shift: the large one). */
export const PANEL_KEYBOARD_STEP = 16;
export const PANEL_KEYBOARD_STEP_LARGE = 64;

export function clampPanelWidth(panel: SidePanel, width: number, viewportWidth?: number): number {
  const limits = SIDE_PANEL_WIDTHS[panel];
  let max = limits.max;
  if (viewportWidth && viewportWidth > 0) {
    max = Math.min(max, Math.max(limits.min, Math.floor(viewportWidth * limits.maxViewportFraction)));
  }
  if (!Number.isFinite(width)) return Math.min(limits.default, max);
  return Math.round(Math.min(max, Math.max(limits.min, width)));
}

export interface PanelWidth {
  /** The width to render: the drag preview, else the stored width, within the limits and the window. */
  width: number;
  min: number;
  max: number;
  /** True while the border is dragged (the preview is shown, nothing stored yet). */
  resizing: boolean;
  /** Live width while dragging; null ends the preview. */
  preview: (width: number | null) => void;
  /** Store a width (clamped) for this browser. */
  commit: (width: number) => void;
  /** Back to the default width. */
  reset: () => void;
}

/** A side panel's width: stored per browser, previewed while its border is dragged. */
export function usePanelWidth(panel: SidePanel): PanelWidth {
  const limits = SIDE_PANEL_WIDTHS[panel];
  const [stored, setStored] = useLocalStorage<number>({
    key: WIDTH_STORAGE_KEYS[panel],
    defaultValue: limits.default,
    getInitialValueInEffect: false,
  });
  const [previewWidth, setPreviewWidth] = useState<number | null>(null);
  const { width: viewportWidth } = useViewportSize();
  const max = clampPanelWidth(panel, limits.max, viewportWidth);
  const stable = typeof stored === 'number' ? stored : limits.default;

  return {
    width: clampPanelWidth(panel, previewWidth ?? stable, viewportWidth),
    min: limits.min,
    max,
    resizing: previewWidth !== null,
    preview: setPreviewWidth,
    commit: (width) => {
      setPreviewWidth(null);
      // Stored within the absolute limits only: a small window does not shrink the remembered width.
      setStored(clampPanelWidth(panel, width));
    },
    reset: () => {
      setPreviewWidth(null);
      setStored(limits.default);
    },
  };
}

/** "Hide the navigation panel (Ctrl+B)": a hint with the toggle action's shortcut. */
export function toggleHint(verb: 'Show' | 'Hide', label: string, actionId: string): string {
  const shortcut = getAction(actionId).shortcuts?.[0];
  return `${verb} the ${label}${shortcut ? ` (${formatShortcut(shortcut)})` : ''}`;
}
