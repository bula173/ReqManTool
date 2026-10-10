import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from '@tanstack/react-query';
import { API_BASE_URL, ApiError, api, enableServerSession, setCsrfToken } from './client';
import { notifications } from '@mantine/notifications';
import type {
  AccessSyncResult,
  AdminUser,
  AuditPage,
  AuthSession,
  EditionInfo,
  GroupRole,
  ProjectMembers,
  ProjectRole,
  ProviderInfo,
} from './types';

/**
 * Query keys of the edition and the server-edition session. Under `version`
 * (like `about`): kept across data directory switches (`clearDatasetCache`)
 * and refetched with it when live sync reconnects, which also notices a
 * session that ended meanwhile.
 */
export const authKeys = {
  edition: ['version', 'edition'] as const,
  session: ['version', 'session'] as const,
  /** Admin pages (server edition): the user list, and the members of one project. */
  adminUsers: ['admin', 'users'] as const,
  members: (projectId: string) => ['admin', 'members', projectId] as const,
  /** The audit log (server admins), per action filter (`''` = all). */
  audit: (action: string) => ['admin', 'audit', action] as const,
};

/**
 * After a change of roles (here, or a `permissions_changed` live-sync event):
 * refetch the user's own roles, the admin pages and every project query (a
 * project may have become visible, hidden, editable or read-only).
 */
export function invalidatePermissions(qc: QueryClient): void {
  qc.invalidateQueries({ queryKey: authKeys.session });
  qc.invalidateQueries({ queryKey: ['admin'] });
  qc.invalidateQueries({ queryKey: ['projects'] });
}

/** GET /edition: local (no login) or server (login needed). Public; fetched once at start-up. */
export function useEdition(): UseQueryResult<EditionInfo> {
  return useQuery({
    queryKey: authKeys.edition,
    queryFn: async () => {
      const edition = await api.get<EditionInfo>('/edition');
      // From now on every request carries the session cookie.
      if (edition.mode === 'server') enableServerSession();
      return edition;
    },
    staleTime: Infinity,
    // Short: the app waits for it (a failure shows the app as in the local edition).
    retry: 1,
    retryDelay: 250,
  });
}

/** GET /auth/me in the server edition: the session, or `null` when not logged in (401). */
export function useAuthSession(enabled: boolean): UseQueryResult<AuthSession | null> {
  return useQuery({
    queryKey: authKeys.session,
    queryFn: async () => {
      try {
        const session = await api.get<AuthSession>('/auth/me');
        setCsrfToken(session.csrf_token);
        return session;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          setCsrfToken(null);
          return null;
        }
        throw error;
      }
    },
    enabled,
    staleTime: 5 * 60_000,
    retry: 1,
  });
}

/** Where a login button sends the browser: the backend starts the provider's login and returns to `nextPath`. */
export function loginUrl(loginPath: string, nextPath: string): string {
  return `${API_BASE_URL}${loginPath}?next=${encodeURIComponent(nextPath)}`;
}

/** POST /auth/logout: ends the session (the cookie is cleared by the response). */
export function logout(): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/auth/logout');
}

// --- admin pages (server edition) -------------------------------------------------

/** GET /admin/users (server admins). */
export function useAdminUsers(enabled: boolean): UseQueryResult<{ users: AdminUser[] }> {
  return useQuery({
    queryKey: authKeys.adminUsers,
    queryFn: () => api.get<{ users: AdminUser[] }>('/admin/users'),
    enabled,
  });
}

/** PUT /admin/users/{id}: activate/deactivate, grant/revoke server admin (server admins; never oneself). */
export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, ...flags }: { userId: string; active?: boolean; server_admin?: boolean }) =>
      api.put<AdminUser>(`/admin/users/${encodeURIComponent(userId)}`, flags),
    onSuccess: () => invalidatePermissions(qc),
  });
}

/** GET /admin/projects/{id}/members (project admins of it, server admins). */
export function useProjectMembers(projectId: string | null): UseQueryResult<ProjectMembers> {
  return useQuery({
    queryKey: authKeys.members(projectId ?? ''),
    queryFn: () => api.get<ProjectMembers>(`/admin/projects/${encodeURIComponent(projectId ?? '')}/members`),
    enabled: !!projectId,
  });
}

/** PUT /admin/projects/{id}/members: add a user (by e-mail, user name or id) or change their role. */
export function useSetProjectMember(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ user, role }: { user: string; role: ProjectRole }) =>
      api.put<ProjectMembers>(`/admin/projects/${encodeURIComponent(projectId)}/members`, { user, role }),
    onSuccess: (data) => {
      qc.setQueryData(authKeys.members(projectId), data);
      invalidatePermissions(qc);
    },
  });
}

/** DELETE /admin/projects/{id}/members/{userId}. */
export function useRemoveProjectMember(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api.delete<ProjectMembers>(
        `/admin/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`,
      ),
    onSuccess: (data) => {
      qc.setQueryData(authKeys.members(projectId), data);
      invalidatePermissions(qc);
    },
  });
}

/**
 * POST /admin/access/sync ("Sync access now"): look up the roles that come from repository
 * permissions again, for every logged-in user — of one project (its project admins) or, without a
 * project, of all (server admins).
 */
export function useSyncAccess() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string | null) =>
      api.post<AccessSyncResult>('/admin/access/sync', projectId ? { project_id: projectId } : {}),
    onSuccess: () => invalidatePermissions(qc),
  });
}

/** POST /admin/providers/test: test a provider connection (server admins). */
export function useTestProvider() {
  return useMutation({
    mutationFn: (providerId: string) =>
      api.post<{ ok: boolean; message: string; issuer?: string; authorization_endpoint?: string }>('/admin/providers/test', { provider_id: providerId }),
    onSuccess: (result) => {
      const message = result.ok ? 'Connection successful' : `Connection failed: ${result.message}`;
      const color = result.ok ? 'green' : 'red';
      notifications.show({ title: 'Provider test', message, color });
    },
    onError: (error) => {
      notifications.show({ color: 'red', title: 'Test failed', message: error instanceof ApiError ? error.message : 'Unknown error' });
    },
  });
}

/** GET /admin/providers: list all configured providers (server admins). */
export function useAdminProviders(enabled: boolean): UseQueryResult<{ providers: ProviderInfo[] }> {
  return useQuery({
    queryKey: ['admin', 'providers'] as const,
    queryFn: () => api.get<{ providers: ProviderInfo[] }>('/admin/providers'),
    enabled,
  });
}

/** GET /admin/group-roles: list all configured group role mappings (server admins). */
export function useAdminGroupRoles(enabled: boolean): UseQueryResult<{ group_roles: GroupRole[] }> {
  return useQuery({
    queryKey: ['admin', 'group_roles'] as const,
    queryFn: () => api.get<{ group_roles: GroupRole[] }>('/admin/group-roles'),
    enabled,
  });
}

/** POST /admin/group-roles: create a new group role mapping (server admins). */
export function useCreateGroupRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { group: string; role: string; projects: string[] }) =>
      api.post<{ group: string; role: string; projects: string[] }>('/admin/group-roles', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin', 'group_roles'] });
      notifications.show({ message: 'Group role created', color: 'green' });
    },
    onError: (error) => {
      notifications.show({ color: 'red', title: 'Create failed', message: error instanceof ApiError ? error.message : 'Unknown error' });
    },
  });
}

/** PUT /admin/group-roles/{group}: update a group role mapping (server admins). */
export function useUpdateGroupRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ group, role, projects }: { group: string; role?: string; projects?: string[] }) =>
      api.put<{ group: string; role: string; projects: string[] }>(`/admin/group-roles/${encodeURIComponent(group)}`, { role, projects }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin', 'group_roles'] });
      notifications.show({ message: 'Group role updated', color: 'green' });
    },
    onError: (error) => {
      notifications.show({ color: 'red', title: 'Update failed', message: error instanceof ApiError ? error.message : 'Unknown error' });
    },
  });
}

/** DELETE /admin/group-roles/{group}: delete a group role mapping (server admins). */
export function useDeleteGroupRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (group: string) =>
      api.delete<{ deleted: string }>(`/admin/group-roles/${encodeURIComponent(group)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin', 'group_roles'] });
      notifications.show({ message: 'Group role deleted', color: 'green' });
    },
    onError: (error) => {
      notifications.show({ color: 'red', title: 'Delete failed', message: error instanceof ApiError ? error.message : 'Unknown error' });
    },
  });
}

/** Entries per page of the audit log. */
export const AUDIT_PAGE_SIZE = 100;

/**
 * GET /admin/audit (server admins): the audit log, newest first, page by page
 * (`fetchNextPage` loads older entries). `action` filters by an action or a
 * prefix ending in `.` (`auth.`, `admin.`, `project.`, `module.`); `''` = all.
 */
export function useAuditLog(action: string, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: authKeys.audit(action),
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: String(AUDIT_PAGE_SIZE) });
      if (pageParam !== null) params.set('before', String(pageParam));
      if (action) params.set('action', action);
      return api.get<AuditPage>(`/admin/audit?${params.toString()}`);
    },
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before,
    enabled,
  });
}
