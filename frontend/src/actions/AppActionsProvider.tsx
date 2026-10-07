import { useComputedColorScheme, useMantineColorScheme } from '@mantine/core';
import { useLocalStorage } from '@mantine/hooks';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  subscribeDatasetCleared,
  useConfig,
  useCopyRequirement,
  useEnableHistory,
  useGitPull,
  useGitPush,
  useGitStatus,
  useModule,
  useMoveRequirement,
  useProject,
  usePurgeModule,
  usePurgeProject,
  useRemoveRequirement,
  useReorderRequirement,
  useRequirements,
  useSetViewColumnWidths,
  useTemplates,
  useViews,
} from '../api/queries';
import { AppDialogs } from '../layout/AppDialogs';
import { AppActionsContext, type AppActionsValue } from './context';
import { contextFor, getAction, shortcutAction } from './registry';
import { hasTextSelection, isShortcutBlockedTarget } from './shortcuts';
import type { ActionTargetOverrides, AppCommands, AppState, DialogRequest, RequirementClipboard } from './types';

const DETAIL_PANEL_STORAGE_KEY = 'reqman.detailPanelOpen';

/**
 * Owns the state the action registry works on — route ids and their loaded
 * project/module/requirements, the requirement selection and clipboard, the
 * active attribute view, the detail panel and colour scheme — plus the
 * dialogs actions open and the global keyboard shortcuts. Mounted once in
 * the app shell, so the menu bar, the module page and its context menu all
 * see the same state.
 */
export function AppActionsProvider({ children }: { children: ReactNode }) {
  const params = useParams<{ projectId?: string; moduleId?: string }>();
  const projectId = params.projectId ?? null;
  const moduleId = params.projectId ? (params.moduleId ?? null) : null;
  const navigate = useNavigate();

  // Same query keys as the pages use, so these share the pages' cache entries.
  const projectQuery = useProject(projectId ?? undefined);
  const moduleQuery = useModule(projectId ?? undefined, moduleId ?? undefined);
  const requirementsQuery = useRequirements(projectId ?? undefined, moduleId ?? undefined);
  const viewsQuery = useViews(projectId ?? undefined);
  const templatesQuery = useTemplates();
  const gitStatusQuery = useGitStatus(projectId ?? undefined);
  const enableHistory = useEnableHistory(projectId ?? '');
  const gitPush = useGitPush(projectId ?? '');
  const gitPull = useGitPull(projectId ?? '');
  const setViewColumnWidths = useSetViewColumnWidths(projectId ?? '');
  // The data folder the clipboard and selection belong to (null until known).
  const dataDir = useConfig().data?.data_dir ?? null;

  const copyRequirement = useCopyRequirement(projectId ?? '', moduleId ?? '');
  const moveRequirement = useMoveRequirement(projectId ?? '', moduleId ?? '');
  const reorderRequirement = useReorderRequirement(projectId ?? '', moduleId ?? '');
  const removeRequirement = useRemoveRequirement(projectId ?? '', moduleId ?? '');
  const purgeModule = usePurgeModule();
  const purgeProject = usePurgeProject();

  const { setColorScheme } = useMantineColorScheme();
  const colorScheme = useComputedColorScheme('light');
  const [detailPanelOpen, setDetailPanelOpen] = useLocalStorage<boolean>({
    key: DETAIL_PANEL_STORAGE_KEY,
    defaultValue: true,
    // Read synchronously so a hidden panel does not flash open on reload.
    getInitialValueInEffect: false,
  });

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

  const commands: AppCommands = useMemo(
    () => ({
      navigate: (path) => navigate(path),
      openDialog: (request) => setDialog(request),
      setClipboard: (entry) => setClipboardEntry(entry ? { dataDir, clipboard: entry } : null),
      selectRequirement: (id) => setSelection({ moduleKey, id }),
      setActiveView: (viewId) => setActiveViewState({ projectId, viewId }),
      setViewColumnWidths: (viewId, columnWidths) => setViewColumnWidths.mutateAsync({ viewId, columnWidths }),
      setDetailPanelOpen,
      setColorScheme,
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
        move: (id, parentId) => moveRequirement.mutateAsync({ id, parentId }),
        reorder: (id, direction) => reorderRequirement.mutateAsync({ id, direction }),
        remove: (id) => removeRequirement.mutateAsync(id),
      },
    }),
    // The mutation objects change identity on every state change; their
    // `mutateAsync` functions are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      navigate,
      dataDir,
      moduleKey,
      projectId,
      setDetailPanelOpen,
      setColorScheme,
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
    ],
  );

  const state: AppState = {
    projectId,
    moduleId,
    project: projectQuery.data ?? null,
    module: moduleId ? (moduleQuery.data ?? null) : null,
    requirements,
    selectedRequirement,
    clipboard,
    views,
    gitStatus: projectId ? (gitStatusQuery.data ?? null) : null,
    activeViewId,
    detailPanelOpen,
    colorScheme,
    templates: {
      data: templatesQuery.data ?? [],
      isLoading: templatesQuery.isLoading,
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

  // Event handlers (shortcuts, menu clicks) read the latest state through a ref.
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  });

  const run = useCallback((id: string, targets?: ActionTargetOverrides): boolean => {
    const action = getAction(id);
    const ctx = contextFor(action, stateRef.current, targets);
    if (!action.isAvailable(ctx)) return false;
    action.run(ctx);
    return true;
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || isShortcutBlockedTarget(event.target)) return;
      // With page text selected, Ctrl/Cmd+C/X copy that text as usual.
      const key = event.key.toLowerCase();
      if ((key === 'c' || key === 'x') && hasTextSelection()) return;
      const action = shortcutAction(event, stateRef.current);
      if (!action) return;
      event.preventDefault();
      action.run(contextFor(action, stateRef.current));
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const isAvailable = (id: string, targets?: ActionTargetOverrides): boolean => {
    const action = getAction(id);
    return action.isAvailable(contextFor(action, state, targets));
  };

  const value: AppActionsValue = { state, run, isAvailable };

  return (
    <AppActionsContext.Provider value={value}>
      {children}
      <AppDialogs dialog={dialog} onClose={() => setDialog(null)} />
    </AppActionsContext.Provider>
  );
}
