import { useMutation, useQuery, useQueryClient, type QueryClient, type UseQueryResult } from '@tanstack/react-query';
import { ApiError, api } from './client';
import type { ModuleLock, ModuleLockResult, ModuleLocks } from './types';

/**
 * Module edit locks (server edition only): a user checks out a module for
 * exclusive editing; everybody else gets it read-only until it is released,
 * broken by a project admin or expires after `idle_minutes` without changes.
 *
 * The key is under the project's keys, so a `changed` or
 * `permissions_changed` live-sync event refetches it too; a
 * `module_locks_changed` event refetches it alone (`invalidateModuleLocks`).
 */
export const moduleLockKeys = {
  project: (projectId: string) => ['projects', projectId, 'module-locks'] as const,
};

/** HTTP status of a change refused because another user checked out the module (or it must be checked out). */
export const MODULE_LOCKED_STATUS = 423;

export function invalidateModuleLocks(qc: QueryClient, projectId: string): void {
  qc.invalidateQueries({ queryKey: moduleLockKeys.project(projectId) });
}

/**
 * GET /projects/{id}/module-locks. `enabled` only in the server edition (the
 * local edition has no such route). Refetched every minute so expired locks
 * disappear without an event.
 */
export function useModuleLocks(projectId: string | null | undefined, enabled: boolean): UseQueryResult<ModuleLocks> {
  return useQuery({
    queryKey: moduleLockKeys.project(projectId ?? ''),
    queryFn: () => api.get<ModuleLocks>(`/projects/${encodeURIComponent(projectId ?? '')}/module-locks`),
    enabled: enabled && !!projectId,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

/** The live lock of a module in a lock list (an expired one counts as none). */
export function findModuleLock(locks: ModuleLocks | null | undefined, moduleId: string | null | undefined): ModuleLock | null {
  if (!locks || !moduleId) return null;
  const lock = locks.locks.find((l) => l.module_id === moduleId);
  if (!lock) return null;
  return Date.parse(lock.expires_at) > Date.now() ? lock : null;
}

/** "Checked out by Alice since 8 Oct 2026, 14:03 (reason)". */
export function describeModuleLock(lock: ModuleLock): string {
  const since = new Date(lock.since);
  const when = Number.isNaN(since.getTime()) ? lock.since : since.toLocaleString();
  const who = lock.user_email ? `${lock.user_name} (${lock.user_email})` : lock.user_name;
  return `Checked out for editing by ${who} since ${when}${lock.reason ? `: ${lock.reason}` : ''}`;
}

function lockPath(projectId: string, moduleId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/lock`;
}

/** After a lock change (or a refused write, 423): refetch the project's locks. */
function onLockChange(qc: QueryClient, projectId: string) {
  invalidateModuleLocks(qc, projectId);
}

/** PUT .../lock: check out (again by the owner: keeps it alive, replaces the reason). */
export function useCheckOutModule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, moduleId, reason }: { projectId: string; moduleId: string; reason?: string }) =>
      api.put<ModuleLockResult>(lockPath(projectId, moduleId), { reason: reason ?? null }),
    onSuccess: (_data, vars) => onLockChange(qc, vars.projectId),
    onError: (err, vars) => {
      if (err instanceof ApiError && err.status === MODULE_LOCKED_STATUS) onLockChange(qc, vars.projectId);
    },
  });
}

/** DELETE .../lock: release one's own lock. */
export function useReleaseModule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, moduleId }: { projectId: string; moduleId: string }) =>
      api.delete<ModuleLockResult>(lockPath(projectId, moduleId)),
    onSettled: (_data, _err, vars) => onLockChange(qc, vars.projectId),
  });
}

/** POST .../lock/break: release anyone's lock (project admins, server admins). */
export function useBreakModuleLock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, moduleId }: { projectId: string; moduleId: string }) =>
      api.post<ModuleLockResult>(`${lockPath(projectId, moduleId)}/break`),
    onSettled: (_data, _err, vars) => onLockChange(qc, vars.projectId),
  });
}
