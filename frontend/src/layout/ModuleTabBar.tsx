import { ActionIcon, Loader } from '@mantine/core';
import { IconAlertTriangle, IconX } from '@tabler/icons-react';
import { useEffect, useRef, useState, type DragEvent } from 'react';
import { useAppActions } from '../actions/context';
import { MODULE_TAB_MENU, moduleLockOf } from '../actions/registry';
import { contextMenuTargetProps } from '../actions/shortcuts';
import { useModule, useProject } from '../api/queries';
import { ModuleLockBadge } from '../components/ModuleLockBadge';
import { ModuleTypeIcon } from '../components/ModuleTypeIcon';
import { hasRequirementDragData, TAB_DRAG_MIME } from '../components/requirementDrag';
import { tabKey, type ModuleTabRef } from './moduleTabs';

/** How long a dragged requirement must hover a tab before the tab opens (to drop it in that module). */
const DRAG_HOVER_OPEN_MS = 600;

function hasTabDragData(event: DragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes(TAB_DRAG_MIME);
}

interface ModuleTabItemProps {
  tab: ModuleTabRef;
  index: number;
  active: boolean;
  dropBefore: boolean;
  onDropIndicator: (index: number | null) => void;
}

function ModuleTabItem({ tab, index, active, dropBefore, onDropIndicator }: ModuleTabItemProps) {
  const { state, run, openMenu } = useAppActions();
  const { commands } = state;
  const projectQuery = useProject(tab.projectId);
  const moduleQuery = useModule(tab.projectId, tab.moduleId);
  const hoverTimer = useRef<number | null>(null);

  const clearHoverTimer = () => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
  };
  useEffect(() => clearHoverTimer, []);

  const loading = projectQuery.isLoading || moduleQuery.isLoading;
  const failed = projectQuery.isError || moduleQuery.isError;
  const projectName = projectQuery.data?.name ?? tab.projectId;
  const moduleName = moduleQuery.data?.name ?? tab.moduleId;
  const label = `${projectName} · ${moduleName}`;
  const open = () => commands.openModule(tab.projectId, tab.moduleId);
  const close = () => run('tab.close', { tab });

  return (
    <div
      role="tab"
      aria-selected={active}
      tabIndex={0}
      className="reqman-tab"
      data-drop-before={dropBefore || undefined}
      data-failed={failed || undefined}
      title={failed ? `${label} (could not be loaded; close this tab)` : label}
      {...contextMenuTargetProps}
      draggable
      onClick={open}
      onMouseDown={(e) => {
        // Middle click closes (and must not start auto-scroll).
        if (e.button === 1) e.preventDefault();
      }}
      onAuxClick={(e) => {
        if (e.button === 1) {
          e.preventDefault();
          close();
        }
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          open();
        } else if (e.key === 'Delete') {
          e.preventDefault();
          close();
        } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          e.preventDefault();
          const sibling =
            e.key === 'ArrowLeft' ? e.currentTarget.previousElementSibling : e.currentTarget.nextElementSibling;
          if (sibling instanceof HTMLElement) sibling.focus();
        }
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        openMenu({ x: e.clientX, y: e.clientY, label, entries: MODULE_TAB_MENU, targets: { tab } });
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData(TAB_DRAG_MIME, tabKey(tab));
        e.dataTransfer.effectAllowed = 'move';
      }}
      onDragOver={(e) => {
        if (hasTabDragData(e)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
          onDropIndicator(index);
        } else if (hasRequirementDragData(e.dataTransfer) && !active && hoverTimer.current === null) {
          // A requirement dragged over a tab: open that module so it can be dropped on a row there.
          hoverTimer.current = window.setTimeout(() => {
            hoverTimer.current = null;
            open();
          }, DRAG_HOVER_OPEN_MS);
        }
      }}
      onDragLeave={(e) => {
        if (e.relatedTarget instanceof Node && e.currentTarget.contains(e.relatedTarget)) return;
        clearHoverTimer();
        onDropIndicator(null);
      }}
      onDrop={(e) => {
        clearHoverTimer();
        onDropIndicator(null);
        if (!hasTabDragData(e)) return;
        e.preventDefault();
        const key = e.dataTransfer.getData(TAB_DRAG_MIME);
        if (key) commands.moveTab(key, index);
      }}
    >
      {loading && <Loader size={12} />}
      {failed && <IconAlertTriangle size={14} color="var(--mantine-color-red-filled)" aria-hidden />}
      {!loading && !failed && <ModuleTypeIcon module={moduleQuery.data} size={14} style={{ flexShrink: 0 }} />}
      <span className="reqman-tab-label">{label}</span>
      {/* Server edition: the module's edit lock (known for the open project's modules). */}
      <ModuleLockBadge lock={moduleLockOf(state, tab.projectId, tab.moduleId)} userId={state.userId} compact />
      <ActionIcon
        variant="subtle"
        color="gray"
        size="xs"
        aria-label={`Close ${label}`}
        tabIndex={-1}
        onClick={(e) => {
          e.stopPropagation();
          close();
        }}
      >
        <IconX size={12} />
      </ActionIcon>
    </div>
  );
}

/**
 * The tab bar of the open modules (above the page). Click: switch; middle
 * click, × or Delete: close; drag: reorder; right click: the tab menu (close,
 * close others/to the right, move, detach). A requirement dragged over a tab
 * opens it after a moment, so it can be dropped on a row of that module
 * (drag-and-drop linking). Hidden while no tab is open.
 */
export function ModuleTabBar() {
  const { state } = useAppActions();
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  if (state.tabs.length === 0) return null;
  const activeKey = state.projectId && state.moduleId ? `${state.projectId}/${state.moduleId}` : null;

  return (
    <div className="reqman-tab-bar" role="tablist" aria-label="Open modules">
      {state.tabs.map((tab, index) => (
        <ModuleTabItem
          key={tabKey(tab)}
          tab={tab}
          index={index}
          active={tabKey(tab) === activeKey}
          dropBefore={dropIndex === index}
          onDropIndicator={setDropIndex}
        />
      ))}
    </div>
  );
}
