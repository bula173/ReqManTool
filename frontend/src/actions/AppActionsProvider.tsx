import { useComputedColorScheme, useMantineColorScheme } from '@mantine/core';
import { useLocalStorage, useMediaQuery } from '@mantine/hooks';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api/client';
import {
  subscribeDatasetCleared,
  useConfig,
  useCopyRequirement,
  useEnableHistory,
  useGitPull,
  useGitPush,
  useGitStatus,
  useModule,
  useModules,
  useMoveRequirement,
  useProject,
  usePurgeModule,
  usePurgeProject,
  useRemoveRequirement,
  useReorderAnyModule,
  useReorderRequirement,
  useRequirements,
  useSetViewColumnWidths,
  useTemplates,
  useUnlinkRequirements,
  useViews,
  type FindRequirementResult,
} from '../api/queries';
import { VERIFICATION_COLUMN_STORAGE_KEY } from '../hooks/columnLayout';
import { AppDialogs } from '../layout/AppDialogs';
import { LEFT_PANEL_TAB_STORAGE_KEY, type LeftPanelTab } from '../layout/leftPanel';
import {
  ATTACH_MESSAGE_TYPE,
  isAttachMessage,
  isDetachedPath,
  modulePath,
  openDetachedWindow,
  projectPath,
  tabKey,
  useModuleTabs,
  type AttachMessage,
} from '../layout/moduleTabs';
import {
  DETAIL_PANEL_OPEN_STORAGE_KEY,
  LEFT_PANEL_OPEN_STORAGE_KEY,
  NARROW_LAYOUT_QUERY,
  type SidePanel,
} from '../layout/sidePanels';
import { AppActionsContext, type AppActionsValue } from './context';
import { ContextMenuHost } from './ContextMenuHost';
import { contextFor, getAction, isSupported, shortcutAction } from './registry';
import { CONTEXT_MENU_TARGET_ATTRIBUTE, hasTextSelection, isShortcutBlockedTarget } from './shortcuts';
import type {
  ActionTargetOverrides,
  AppCommands,
  AppState,
  DialogRequest,
  MenuRequest,
  RequirementClipboard,
} from './types';

/** Shift+F10 or the context menu key: "open the editing menu". */
function isContextMenuKey(event: KeyboardEvent): boolean {
  if (event.ctrlKey || event.metaKey || event.altKey) return false;
  return event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey);
}

/**
 * The element whose editing menu Shift+F10 opens: the focused element's menu
 * target, else the selected requirement's table row.
 */
function keyboardMenuTarget(selectedRequirementId: string | null): Element | null {
  const active = document.activeElement;
  const focused = active instanceof Element ? active.closest(`[${CONTEXT_MENU_TARGET_ATTRIBUTE}]`) : null;
  if (focused) return focused;
  if (!selectedRequirementId) return null;
  return document.querySelector(`[data-requirement-row="${CSS.escape(selectedRequirementId)}"]`);
}

/**
 * Owns the state the action registry works on — route ids and their loaded
 * project/module/requirements, the requirement selection and clipboard, the
 * active attribute view, the detail panel and colour scheme — plus the
 * dialogs actions open, the one context menu (`openMenu`) and the global
 * keyboard shortcuts (incl. Shift+F10 / the menu key for the editing menu).
 * Mounted once in the app shell, so the menu bar, the pages, the sidebar and
 * every context menu see the same state.
 */
export function AppActionsProvider({ children }: { children: ReactNode }) {
  const params = useParams<{ projectId?: string; moduleId?: string }>();
  const projectId = params.projectId ?? null;
  const moduleId = params.projectId ? (params.moduleId ?? null) : null;
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // A detached module window (minimal chrome, no tab bar).
  const detached = isDetachedPath(pathname);

  // Same query keys as the pages use, so these share the pages' cache entries.
  const projectQuery = useProject(projectId ?? undefined);
  const moduleQuery = useModule(projectId ?? undefined, moduleId ?? undefined);
  const requirementsQuery = useRequirements(projectId ?? undefined, moduleId ?? undefined);
  const modulesQuery = useModules(projectId ?? undefined);
  const viewsQuery = useViews(projectId ?? undefined);
  // The templates list is fetched lazily: only once a template menu or picker opened.
  const [templatesRequested, setTemplatesRequested] = useState(false);
  const templatesQuery = useTemplates(templatesRequested);
  const gitStatusQuery = useGitStatus(projectId ?? undefined);
  const enableHistory = useEnableHistory(projectId ?? '');
  const gitPush = useGitPush(projectId ?? '');
  const gitPull = useGitPull(projectId ?? '');
  const setViewColumnWidths = useSetViewColumnWidths(projectId ?? '');
  // The data folder the clipboard and selection belong to (null until known).
  const dataDir = useConfig().data?.data_dir ?? null;
  // Module tabs: remembered per browser and data folder.
  const moduleTabs = useModuleTabs(dataDir);

  const copyRequirement = useCopyRequirement(projectId ?? '', moduleId ?? '');
  const moveRequirement = useMoveRequirement(projectId ?? '', moduleId ?? '');
  const reorderRequirement = useReorderRequirement(projectId ?? '', moduleId ?? '');
  const removeRequirement = useRemoveRequirement(projectId ?? '', moduleId ?? '');
  const unlinkRequirements = useUnlinkRequirements(projectId ?? '');
  const reorderModule = useReorderAnyModule();
  const purgeModule = usePurgeModule();
  const purgeProject = usePurgeProject();

  const { setColorScheme } = useMantineColorScheme();
  const colorScheme = useComputedColorScheme('light');
  // Side panels. Wide screens: open/collapsed per browser (localStorage).
  const [storedDetailPanelOpen, setStoredDetailPanelOpen] = useLocalStorage<boolean>({
    key: DETAIL_PANEL_OPEN_STORAGE_KEY,
    defaultValue: true,
    // Read synchronously so a hidden panel does not flash open on reload.
    getInitialValueInEffect: false,
  });
  const [storedLeftPanelOpen, setStoredLeftPanelOpen] = useLocalStorage<boolean>({
    key: LEFT_PANEL_OPEN_STORAGE_KEY,
    defaultValue: true,
    getInitialValueInEffect: false,
  });
  // Narrow screens: both panels start collapsed; one may be open as an
  // overlay, for the page it was opened on (navigating closes it). The
  // stored wide-screen state is not touched there.
  const narrowLayout = useMediaQuery(NARROW_LAYOUT_QUERY, false, { getInitialValueInEffect: false });
  const [overlay, setOverlay] = useState<{ panel: SidePanel; pathname: string } | null>(null);
  const overlayPanel = narrowLayout && overlay?.pathname === pathname ? overlay.panel : null;
  const leftPanelOpen = narrowLayout ? overlayPanel === 'left' : storedLeftPanelOpen !== false;
  const detailPanelOpen = narrowLayout ? overlayPanel === 'right' : storedDetailPanelOpen !== false;
  // The sidebar's active tab (shared with `Sidebar` through the same storage key).
  const [, setLeftPanelTab] = useLocalStorage<LeftPanelTab>({
    key: LEFT_PANEL_TAB_STORAGE_KEY,
    defaultValue: 'workspace',
    getInitialValueInEffect: false,
  });
  const [verificationColumn, setVerificationColumn] = useLocalStorage<boolean>({
    key: VERIFICATION_COLUMN_STORAGE_KEY,
    defaultValue: false,
    getInitialValueInEffect: false,
  });
  const [menu, setMenu] = useState<MenuRequest | null>(null);

  // Project, module and requirement ids repeat across data folders (clones,
  // projects created from one template), so the clipboard remembers the data
  // folder it was filled in and reads as empty in any other folder; a folder
  // switch (here or via `root_changed`) also clears it and the selection. A
  // cut in one folder can never move a requirement of another.
  const [clipboardEntry, setClipboardEntry] = useState<{
    dataDir: string | null;
    clipboard: RequirementClipboard;
  } | null>(null);
  const clipboard = clipboardEntry?.dataDir === dataDir ? clipboardEntry.clipboard : null;
  const [dialog, setDialog] = useState<DialogRequest | null>(null);
  // Selection and active view are remembered for the module / project they
  // were made in, so switching modules or projects starts clean (and a data
  // folder switch clears the selection, see below).
  const moduleKey = projectId && moduleId ? `${projectId}/${moduleId}` : null;
  const [selection, setSelection] = useState<{ moduleKey: string | null; id: string | null }>({
    moduleKey: null,
    id: null,
  });
  const [activeView, setActiveViewState] = useState<{ projectId: string | null; viewId: string | null }>({
    projectId: null,
    viewId: null,
  });

  const requirements = useMemo(
    () => (moduleId ? (requirementsQuery.data ?? []) : []),
    [moduleId, requirementsQuery.data],
  );
  const selectedId = selection.moduleKey === moduleKey ? selection.id : null;
  const selectedRequirement = requirements.find((r) => r.id === selectedId) ?? null;
  const views = useMemo(() => (projectId ? (viewsQuery.data ?? []) : []), [projectId, viewsQuery.data]);
  const requestedViewId = activeView.projectId === projectId ? activeView.viewId : null;
  const activeViewId = views.some((v) => v.id === requestedViewId) ? requestedViewId : null;

  // Opening a module (in a main window) adds its tab.
  const addTab = moduleTabs.add;
  useEffect(() => {
    if (detached || !projectId || !moduleId) return;
    addTab({ projectId, moduleId });
  }, [detached, projectId, moduleId, addTab]);

  // A detached window re-attaches its module through its opener (this window).
  useEffect(() => {
    if (detached) return;
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || !isAttachMessage(event.data)) return;
      const { projectId: attachProjectId, moduleId: attachModuleId } = event.data;
      addTab({ projectId: attachProjectId, moduleId: attachModuleId });
      navigate(modulePath(attachProjectId, attachModuleId));
      setLeftPanelTab('module');
      window.focus();
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [detached, addTab, navigate, setLeftPanelTab]);

  const setPanelOpen = useCallback(
    (panel: SidePanel, open: boolean) => {
      if (narrowLayout) {
        // One overlay at a time; closing only closes this panel's overlay.
        setOverlay((current) => (open ? { panel, pathname } : current?.panel === panel ? null : current));
        return;
      }
      setOverlay(null);
      if (panel === 'left') setStoredLeftPanelOpen(open);
      else setStoredDetailPanelOpen(open);
    },
    [narrowLayout, pathname, setStoredLeftPanelOpen, setStoredDetailPanelOpen],
  );

  const commands: AppCommands = useMemo(() => {
    const closeTabs = (keys: string[]) => {
      const tabs = moduleTabs.tabs;
      moduleTabs.remove(keys);
      // Closing the active tab: show its right (else left) neighbour, else the project page.
      if (detached || !moduleKey || !projectId || !keys.includes(moduleKey)) return;
      const index = tabs.findIndex((t) => tabKey(t) === moduleKey);
      const isKept = (t: (typeof tabs)[number]) => !keys.includes(tabKey(t));
      const next =
        (index >= 0 ? tabs.slice(index + 1).find(isKept) : undefined) ??
        tabs
          .slice(0, Math.max(index, 0))
          .reverse()
          .find(isKept);
      navigate(next ? modulePath(next.projectId, next.moduleId) : projectPath(projectId));
    };
    return {
      navigate: (path) => navigate(path),
      openDialog: (request) => setDialog(request),
      setClipboard: (entry) => setClipboardEntry(entry ? { dataDir, clipboard: entry } : null),
      selectRequirement: (id) => setSelection({ moduleKey, id }),
      setActiveView: (viewId) => setActiveViewState({ projectId, viewId }),
      setViewColumnWidths: (viewId, columnWidths) => setViewColumnWidths.mutateAsync({ viewId, columnWidths }),
      setLeftPanelOpen: (open) => setPanelOpen('left', open),
      setDetailPanelOpen: (open) => setPanelOpen('right', open),
      setColorScheme,
      setVerificationColumn,
      enableHistory: () => enableHistory.mutateAsync(),
      gitPush: () => gitPush.mutateAsync(),
      gitPull: () => gitPull.mutateAsync(),
      download: (url) => {
        const link = document.createElement('a');
        link.href = url;
        link.rel = 'noopener';
        document.body.appendChild(link);
        link.click();
        link.remove();
      },
      openExternal: (url) => {
        window.open(url, '_blank', 'noopener,noreferrer');
      },
      purgeModule: (purgeProjectId, purgeModuleId) =>
        purgeModule.mutateAsync({ projectId: purgeProjectId, moduleId: purgeModuleId }),
      purgeProject: (purgeProjectId) => purgeProject.mutateAsync(purgeProjectId),
      requirements: {
        copy: (id, targetParentId) => copyRequirement.mutateAsync({ id, targetParentId }),
        move: (id, parentId, index) => moveRequirement.mutateAsync({ id, parentId, index }),
        reorder: (id, direction) => reorderRequirement.mutateAsync({ id, direction }),
        remove: (id) => removeRequirement.mutateAsync(id),
      },
      unlink: (configId, sourceId, targetId) =>
        unlinkRequirements.mutateAsync({ config_id: configId, source_id: sourceId, target_id: targetId }),
      reorderModule: (reorderProjectId, reorderModuleId, direction) =>
        reorderModule.mutateAsync({ projectId: reorderProjectId, moduleId: reorderModuleId, direction }),
      goToRequirement: async (requirementId) => {
        if (!projectId) throw new Error('No project is open');
        const result = await api.get<FindRequirementResult>(
          `/projects/${encodeURIComponent(projectId)}/requirements/${encodeURIComponent(requirementId)}`,
        );
        // The module page opens the row's ancestors, selects it, scrolls to it and drops `highlight`.
        // A detached window stays detached (it then shows the other module).
        navigate(`${modulePath(projectId, result.module_id, detached)}?highlight=${encodeURIComponent(requirementId)}`, {
          replace: result.module_id === moduleId,
        });
      },
      showInTree: (requirementId) => {
        setSelection({ moduleKey, id: requirementId });
        setLeftPanelTab('module');
        // The tree must be visible to show anything.
        setPanelOpen('left', true);
      },
      openProject: (openProjectId) => {
        navigate(`/projects/${encodeURIComponent(openProjectId)}`);
        setLeftPanelTab('project');
      },
      openModule: (openProjectId, openModuleId) => {
        navigate(modulePath(openProjectId, openModuleId, detached));
        setLeftPanelTab('module');
      },
      openModuleTab: (tabProjectId, tabModuleId) => moduleTabs.add({ projectId: tabProjectId, moduleId: tabModuleId }),
      closeTabs,
      moveTab: (key, toIndex) => moduleTabs.move(key, toIndex),
      openModuleWindow: (windowProjectId, windowModuleId, highlightId) =>
        openDetachedWindow(windowProjectId, windowModuleId, highlightId) !== null,
      detachTab: (tab) => {
        if (openDetachedWindow(tab.projectId, tab.moduleId) === null) return false;
        closeTabs([tabKey(tab)]);
        return true;
      },
      reattach: () => {
        if (!projectId || !moduleId) return;
        moduleTabs.add({ projectId, moduleId });
        const opener = window.opener as Window | null;
        let posted = false;
        try {
          if (opener && !opener.closed) {
            const message: AttachMessage = { type: ATTACH_MESSAGE_TYPE, projectId, moduleId };
            opener.postMessage(message, window.location.origin);
            opener.focus();
            posted = true;
          }
        } catch {
          // The opener navigated elsewhere: fall through.
        }
        if (posted) window.close();
        // Without an opener (or when the browser keeps this window open), this
        // window becomes a main window showing the module's tab.
        window.setTimeout(() => navigate(modulePath(projectId, moduleId)), posted ? 300 : 0);
      },
      requestTemplates: () => setTemplatesRequested(true),
    };
  },
    // The mutation objects change identity on every state change; their
    // `mutateAsync` functions are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      navigate,
      dataDir,
      detached,
      moduleTabs.tabs,
      moduleTabs.add,
      moduleTabs.remove,
      moduleTabs.move,
      moduleKey,
      projectId,
      moduleId,
      setPanelOpen,
      setLeftPanelTab,
      setColorScheme,
      setVerificationColumn,
      copyRequirement.mutateAsync,
      moveRequirement.mutateAsync,
      reorderRequirement.mutateAsync,
      removeRequirement.mutateAsync,
      purgeModule.mutateAsync,
      purgeProject.mutateAsync,
      enableHistory.mutateAsync,
      gitPush.mutateAsync,
      gitPull.mutateAsync,
      setViewColumnWidths.mutateAsync,
      unlinkRequirements.mutateAsync,
      reorderModule.mutateAsync,
    ],
  );

  const state: AppState = {
    projectId,
    moduleId,
    project: projectQuery.data ?? null,
    module: moduleId ? (moduleQuery.data ?? null) : null,
    modules: projectId ? (modulesQuery.data ?? []) : [],
    requirements,
    selectedRequirement,
    clipboard,
    views,
    gitStatus: projectId ? (gitStatusQuery.data ?? null) : null,
    activeViewId,
    leftPanelOpen,
    detailPanelOpen,
    narrowLayout,
    colorScheme,
    verificationColumn: verificationColumn === true,
    tabs: detached ? [] : moduleTabs.tabs,
    detached,
    templates: {
      data: templatesQuery.data ?? [],
      isLoading: !templatesRequested || templatesQuery.isLoading,
      isError: templatesQuery.isError,
    },
    commands,
  };

  useEffect(
    () =>
      subscribeDatasetCleared(() => {
        setClipboardEntry(null);
        setSelection({ moduleKey: null, id: null });
      }),
    [],
  );

  // When Shift+F10 / the menu key last opened a menu (see the keydown handler).
  const keyboardMenuAt = useRef(-Infinity);
  // Event handlers (shortcuts, menu clicks) read the latest state through a ref.
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  });

  const run = useCallback((id: string, targets?: ActionTargetOverrides): boolean => {
    const action = getAction(id);
    const ctx = contextFor(action, stateRef.current, targets);
    if (!isSupported(action, ctx) || !action.isAvailable(ctx)) return false;
    action.run(ctx);
    return true;
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || isShortcutBlockedTarget(event.target)) return;
      if (isContextMenuKey(event)) {
        // Opens the editing menu of the focused element (or the selected row)
        // through its own contextmenu handler, as a right click would.
        const element = keyboardMenuTarget(stateRef.current.selectedRequirement?.id ?? null);
        if (!element) return;
        event.preventDefault();
        const rect = element.getBoundingClientRect();
        keyboardMenuAt.current = performance.now();
        element.dispatchEvent(
          new MouseEvent('contextmenu', {
            bubbles: true,
            cancelable: true,
            clientX: rect.left + Math.min(24, rect.width / 2),
            clientY: rect.top + Math.min(rect.height, 28),
          }),
        );
        return;
      }
      // With page text selected, Ctrl/Cmd+C/X copy that text as usual.
      const key = event.key.toLowerCase();
      if ((key === 'c' || key === 'x') && hasTextSelection()) return;
      const action = shortcutAction(event, stateRef.current);
      if (!action) return;
      event.preventDefault();
      action.run(contextFor(action, stateRef.current));
    };
    // The browser may still send its own contextmenu event for the key (the
    // menu key on Windows): it would reopen the menu at another position.
    const onNativeContextMenu = (event: MouseEvent) => {
      if (event.isTrusted && performance.now() - keyboardMenuAt.current < 500) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('contextmenu', onNativeContextMenu, true);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('contextmenu', onNativeContextMenu, true);
    };
  }, []);

  const isAvailable = (id: string, targets?: ActionTargetOverrides): boolean => {
    const action = getAction(id);
    const ctx = contextFor(action, state, targets);
    return isSupported(action, ctx) && action.isAvailable(ctx);
  };

  const openMenu = useCallback((request: MenuRequest) => setMenu(request), []);

  const value: AppActionsValue = { state, run, isAvailable, openMenu };

  return (
    <AppActionsContext.Provider value={value}>
      {children}
      <ContextMenuHost request={menu} onClose={() => setMenu(null)} />
      <AppDialogs dialog={dialog} onClose={() => setDialog(null)} />
    </AppActionsContext.Provider>
  );
}
