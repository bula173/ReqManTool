import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { API_BASE_URL, ApiError, api, enableServerSession, setCsrfToken } from './client';
import type { AuthSession, EditionInfo } from './types';

/**
 * Query keys of the edition and the server-edition session. Under `version`
 * (like `about`): kept across data directory switches (`clearDatasetCache`)
 * and refetched with it when live sync reconnects, which also notices a
 * session that ended meanwhile.
 */
export const authKeys = {
  edition: ['version', 'edition'] as const,
  session: ['version', 'session'] as const,
};

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
