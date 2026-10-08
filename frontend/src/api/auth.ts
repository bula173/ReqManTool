import { useMutation, useQuery, useQueryClient, type QueryClient, type UseQueryResult } from '@tanstack/react-query';
import { API_BASE_URL, ApiError, api, enableServerSession, setCsrfToken } from './client';
import type { AdminUser, AuthSession, EditionInfo, ProjectMembers, ProjectRole } from './types';

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
