import { ActionIcon, Tooltip } from '@mantine/core';
import { IconChevronLeft, IconChevronRight } from '@tabler/icons-react';
import { useEffect, useRef, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import { isShortcutBlockedTarget } from '../actions/shortcuts';
import { beginBodyDrag, endBodyDrag } from '../hooks/columnLayout';
import {
  PANEL_KEYBOARD_STEP,
  PANEL_KEYBOARD_STEP_LARGE,
  toggleHint,
  type PanelWidth,
  type SidePanel,
} from './sidePanels';

interface PanelEdgeProps {
  side: SidePanel;
  width: PanelWidth;
  /** e.g. "navigation panel" (used in labels and tooltips). */
  label: string;
  toggleActionId: string;
  onCollapse: () => void;
  /** The panel sits a `md` gap away from its neighbour: the edge is centred in the gap. */
  inGap?: boolean;
}

/**
 * The inner border of an open side panel: drag it to resize the panel
 * (pointer capture, live preview, stored on release), double-click to reset
 * the default width, or focus it and use ArrowLeft / ArrowRight (Shift for
 * bigger steps). The chevron on it collapses the panel to its rail.
 */
export function PanelEdge({ side, width, label, toggleActionId, onCollapse, inGap }: PanelEdgeProps) {
  const drag = useRef<{ pointerId: number; startX: number; startWidth: number; width: number } | null>(null);
  // Dragging the left panel's right border to the right widens it; the right panel's left border the other way.
  const direction = side === 'left' ? 1 : -1;

  // Unmounted mid-drag (collapsed by shortcut, narrow layout): give the body its cursor and text selection back.
  useEffect(
    () => () => {
      if (drag.current) endBodyDrag();
    },
    [],
  );

  const finishDrag = (commit: boolean) => {
    const current = drag.current;
    drag.current = null;
    endBodyDrag();
    if (!current) return;
    if (commit && current.width !== current.startWidth) width.commit(current.width);
    else width.preview(null);
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: width.width, width: width.width };
    width.preview(width.width);
    beginBodyDrag('col-resize');
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const next = Math.min(
      width.max,
      Math.max(width.min, Math.round(current.startWidth + direction * (event.clientX - current.startX))),
    );
    if (next === current.width) return;
    current.width = next;
    width.preview(next);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    event.stopPropagation();
    const step = (event.shiftKey ? PANEL_KEYBOARD_STEP_LARGE : PANEL_KEYBOARD_STEP) * direction;
    width.commit(width.width + (event.key === 'ArrowRight' ? step : -step));
  };

  return (
    <div className="reqman-panel-edge" data-side={side} data-in-gap={inGap || undefined}>
      <div
        className="reqman-panel-resize-handle"
        data-resizing={width.resizing || undefined}
        role="separator"
        aria-orientation="vertical"
        aria-label={`Resize the ${label}`}
        aria-valuenow={width.width}
        aria-valuemin={width.min}
        aria-valuemax={width.max}
        tabIndex={0}
        title="Drag to resize, double-click to reset the width"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={() => finishDrag(true)}
        onPointerCancel={() => finishDrag(false)}
        onLostPointerCapture={() => {
          if (drag.current) finishDrag(true);
        }}
        onDoubleClick={(event) => {
          event.stopPropagation();
          width.reset();
        }}
        onKeyDown={onKeyDown}
      />
      <Tooltip label={toggleHint('Hide', label, toggleActionId)} position={side === 'left' ? 'right' : 'left'} withArrow>
        <ActionIcon
          className="reqman-panel-toggle"
          variant="default"
          radius="xl"
          size="sm"
          aria-label={`Hide the ${label}`}
          onClick={onCollapse}
        >
          {side === 'left' ? <IconChevronLeft size={14} /> : <IconChevronRight size={14} />}
        </ActionIcon>
      </Tooltip>
    </div>
  );
}

interface PanelRailProps {
  side: SidePanel;
  label: string;
  /** The short text shown vertically on the rail. */
  title: string;
  toggleActionId: string;
  onExpand: () => void;
  /** Draw the rail's own border (the left panel's rail lives in the navbar, which has one). */
  bordered?: boolean;
}

/** A collapsed side panel: a thin rail with an expand chevron (clicking anywhere on the rail expands it). */
export function PanelRail({ side, label, title, toggleActionId, onExpand, bordered }: PanelRailProps) {
  return (
    <div className="reqman-panel-rail" data-side={side} data-bordered={bordered || undefined} onClick={onExpand}>
      <Tooltip label={toggleHint('Show', label, toggleActionId)} position={side === 'left' ? 'right' : 'left'} withArrow>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          aria-label={`Show the ${label}`}
          aria-expanded={false}
          onClick={(event) => {
            event.stopPropagation();
            onExpand();
          }}
        >
          {side === 'left' ? <IconChevronRight size={14} /> : <IconChevronLeft size={14} />}
        </ActionIcon>
      </Tooltip>
      <span className="reqman-panel-rail-label" aria-hidden>
        {title}
      </span>
    </div>
  );
}

interface SidePanelOverlayProps {
  side: SidePanel;
  width: PanelWidth;
  label: string;
  toggleActionId: string;
  onClose: () => void;
  children: ReactNode;
}

/**
 * An open side panel on a narrow screen: drawn over the page (from the header
 * to the footer) above a backdrop. A click on the backdrop or Escape (outside
 * text fields, menus and dialogs) closes it.
 */
export function SidePanelOverlay({ side, width, label, toggleActionId, onClose, children }: SidePanelOverlayProps) {
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || isShortcutBlockedTarget(event.target)) return;
      onCloseRef.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  return (
    <>
      <div className="reqman-panel-backdrop" aria-hidden onClick={onClose} />
      <div
        className="reqman-panel-overlay"
        data-side={side}
        role="complementary"
        aria-label={label}
        style={{ width: `min(${width.width}px, calc(100vw - 40px))` }}
      >
        {children}
        <PanelEdge side={side} width={width} label={label} toggleActionId={toggleActionId} onCollapse={onClose} />
      </div>
    </>
  );
}
