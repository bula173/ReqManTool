import { useLocalStorage } from '@mantine/hooks';
import { useCallback, useMemo } from 'react';

/**
 * Two-step linking ("Start link" → "Make link"): the pending link source is
 * remembered per browser (localStorage, never in git-tracked data), so every
 * window of the app — module tabs, detached module windows — sees it and can
 * make the link (the `storage` event keeps them in sync). It is scoped to the
 * data folder it was started in: project and requirement ids repeat across
 * folders, so it reads as empty in any other folder (and a folder switch
 * clears it, see `AppActionsProvider`).
 */

export const PENDING_LINK_STORAGE_KEY = 'reqman.pendingLink';

export interface PendingLinkSource {
  projectId: string;
  moduleId: string;
  requirementId: string;
  /** Display names at the time the link was started (the bar's "from" label). */
  projectName?: string;
  moduleName?: string;
}

interface StoredPendingLink {
  dataDir: string;
  source: PendingLinkSource;
}

function sanitize(value: unknown): StoredPendingLink | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const { dataDir, source } = value as Record<string, unknown>;
  if (typeof dataDir !== 'string' || !source || typeof source !== 'object') return null;
  const { projectId, moduleId, requirementId, projectName, moduleName } = source as Record<string, unknown>;
  if (typeof projectId !== 'string' || typeof moduleId !== 'string' || typeof requirementId !== 'string') return null;
  if (!projectId || !moduleId || !requirementId) return null;
  return {
    dataDir,
    source: {
      projectId,
      moduleId,
      requirementId,
      projectName: typeof projectName === 'string' ? projectName : undefined,
      moduleName: typeof moduleName === 'string' ? moduleName : undefined,
    },
  };
}

/** Whether two pending sources are the same requirement. */
export function isSamePendingSource(a: PendingLinkSource | null | undefined, b: PendingLinkSource | null | undefined) {
  return !!a && !!b && a.projectId === b.projectId && a.requirementId === b.requirementId;
}

export interface PendingLink {
  /** The pending source of the current data folder (null: none, or the folder is not known yet). */
  source: PendingLinkSource | null;
  start: (source: PendingLinkSource) => void;
  /**
   * Clears the pending source. With `only`, only when that is still the
   * pending one (another window may have started a new link meanwhile).
   */
  cancel: (only?: PendingLinkSource) => void;
  /** Clears it when it was started in project `projectId` (the project was removed). */
  cancelProject: (projectId: string) => void;
}

export function usePendingLink(dataDir: string | null): PendingLink {
  const [stored, setStored] = useLocalStorage<StoredPendingLink | null>({
    key: PENDING_LINK_STORAGE_KEY,
    defaultValue: null,
    getInitialValueInEffect: false,
  });
  const source = useMemo(() => {
    const entry = sanitize(stored);
    return entry && dataDir !== null && entry.dataDir === dataDir ? entry.source : null;
  }, [stored, dataDir]);

  const start = useCallback(
    (next: PendingLinkSource) => {
      if (dataDir === null) return;
      setStored({ dataDir, source: next });
    },
    [dataDir, setStored],
  );
  const cancel = useCallback(
    (only?: PendingLinkSource) =>
      setStored((current) => {
        const entry = sanitize(current);
        if (!entry) return null;
        return only && !isSamePendingSource(entry.source, only) ? entry : null;
      }),
    [setStored],
  );
  const cancelProject = useCallback(
    (projectId: string) =>
      setStored((current) => {
        const entry = sanitize(current);
        return entry && (entry.dataDir !== dataDir || entry.source.projectId !== projectId) ? entry : null;
      }),
    [dataDir, setStored],
  );

  return { source, start, cancel, cancelProject };
}
