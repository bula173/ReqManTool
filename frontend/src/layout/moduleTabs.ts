import { useLocalStorage } from '@mantine/hooks';
import { useCallback, useMemo } from 'react';

/**
 * Module tabs: the modules open in the tab bar above the page, remembered per
 * browser (localStorage, never in git-tracked data) and per data folder —
 * project and module ids repeat across folders, so each folder keeps its own
 * list. The active tab is the module route of the window; every browser
 * window shows the same list (it syncs through the `storage` event).
 *
 * A module can also be shown on its own in a detached window (a route under
 * `DETACHED_ROUTE_PREFIX`: minimal chrome, same live sync); re-attaching it
 * puts it back in the tab bar of the window that opened it.
 */

export const MODULE_TABS_STORAGE_KEY = 'reqman.moduleTabs';

/** Route prefix of a detached module window. */
export const DETACHED_ROUTE_PREFIX = '/detached';

/** `window.postMessage` type a detached window sends to its opener to re-attach its module. */
export const ATTACH_MESSAGE_TYPE = 'reqman.attachModule';

export interface ModuleTabRef {
  projectId: string;
  moduleId: string;
}

export interface AttachMessage extends ModuleTabRef {
  type: typeof ATTACH_MESSAGE_TYPE;
}

export function tabKey(tab: ModuleTabRef): string {
  return `${tab.projectId}/${tab.moduleId}`;
}

export function projectPath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}`;
}

/** The module route; `detached`: the route of a detached module window. */
export function modulePath(projectId: string, moduleId: string, detached = false): string {
  const path = `${projectPath(projectId)}/modules/${encodeURIComponent(moduleId)}`;
  return detached ? `${DETACHED_ROUTE_PREFIX}${path}` : path;
}

export function isDetachedPath(pathname: string): boolean {
  return pathname === DETACHED_ROUTE_PREFIX || pathname.startsWith(`${DETACHED_ROUTE_PREFIX}/`);
}

export function isAttachMessage(data: unknown): data is AttachMessage {
  if (!data || typeof data !== 'object') return false;
  const value = data as Record<string, unknown>;
  return (
    value.type === ATTACH_MESSAGE_TYPE && typeof value.projectId === 'string' && typeof value.moduleId === 'string'
  );
}

/**
 * A window-name-safe, reversible encoding of an id: letters, digits and `-`
 * stay, any other UTF-16 unit (including `_`) becomes `_` plus 4 hex digits.
 * The output never contains `_-` nor ends with `_`, so `_-` separates two
 * encoded ids unambiguously (project `a-b` + module `c` ≠ project `a` +
 * module `b-c`).
 */
function windowNamePart(id: string): string {
  return id.replace(/[^A-Za-z0-9-]/g, (c) => `_${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

/**
 * Opens (or, when already open, reuses and focuses) the detached window of a
 * module; `highlightId` selects and reveals a requirement in it. Returns null
 * when the browser blocked the popup.
 */
export function openDetachedWindow(projectId: string, moduleId: string, highlightId?: string): Window | null {
  const path = modulePath(projectId, moduleId, true);
  const url = highlightId ? `${path}?highlight=${encodeURIComponent(highlightId)}` : path;
  // One window per module: the same name reuses it. No `noopener`: the window
  // needs `window.opener` to re-attach.
  const name = `reqman-module-${windowNamePart(projectId)}_-${windowNamePart(moduleId)}`;
  const win = window.open(url, name, 'popup=yes,width=1200,height=800');
  win?.focus();
  return win;
}

type StoredTabs = Record<string, ModuleTabRef[]>;

function sanitizeTabs(value: unknown): ModuleTabRef[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const tabs: ModuleTabRef[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    const { projectId, moduleId } = item as Record<string, unknown>;
    if (typeof projectId !== 'string' || typeof moduleId !== 'string' || !projectId || !moduleId) continue;
    const tab = { projectId, moduleId };
    if (seen.has(tabKey(tab))) continue;
    seen.add(tabKey(tab));
    tabs.push(tab);
  }
  return tabs;
}

function sanitizeStored(value: unknown): StoredTabs {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return value as StoredTabs;
}

export interface ModuleTabs {
  tabs: ModuleTabRef[];
  /** Adds a tab (at the end) unless it is open already. */
  add: (tab: ModuleTabRef) => void;
  remove: (keys: string[]) => void;
  /** Closes every tab of a project (it was removed). */
  removeProject: (projectId: string) => void;
  /** Moves a tab so that it ends up at position `toIndex` (dropped on the tab at that position). */
  move: (key: string, toIndex: number) => void;
}

/** The open module tabs of the data folder `dataDir` (none while it is unknown). */
export function useModuleTabs(dataDir: string | null): ModuleTabs {
  const [stored, setStored] = useLocalStorage<StoredTabs>({
    key: MODULE_TABS_STORAGE_KEY,
    defaultValue: {},
    getInitialValueInEffect: false,
  });
  const tabs = useMemo(
    () => (dataDir === null ? [] : sanitizeTabs(sanitizeStored(stored)[dataDir])),
    [stored, dataDir],
  );

  const update = useCallback(
    (change: (tabs: ModuleTabRef[]) => ModuleTabRef[]) => {
      if (dataDir === null) return;
      setStored((current) => {
        const all = sanitizeStored(current);
        const next = change(sanitizeTabs(all[dataDir]));
        const result = { ...all };
        if (next.length > 0) result[dataDir] = next;
        else delete result[dataDir];
        return result;
      });
    },
    [dataDir, setStored],
  );

  const add = useCallback(
    (tab: ModuleTabRef) =>
      update((current) => (current.some((t) => tabKey(t) === tabKey(tab)) ? current : [...current, tab])),
    [update],
  );
  const remove = useCallback(
    (keys: string[]) => update((current) => current.filter((t) => !keys.includes(tabKey(t)))),
    [update],
  );
  const removeProject = useCallback(
    (projectId: string) => update((current) => current.filter((t) => t.projectId !== projectId)),
    [update],
  );
  const move = useCallback(
    (key: string, toIndex: number) =>
      update((current) => {
        const from = current.findIndex((t) => tabKey(t) === key);
        if (from < 0) return current;
        const next = [...current];
        const [tab] = next.splice(from, 1);
        next.splice(Math.max(0, Math.min(toIndex, next.length)), 0, tab);
        return next;
      }),
    [update],
  );

  return { tabs, add, remove, removeProject, move };
}
