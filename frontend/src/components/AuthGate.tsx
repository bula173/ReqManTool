import { Center, Loader } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, type ReactNode } from 'react';
import { authKeys, logout as logoutRequest, useAuthSession, useEdition } from '../api/auth';
import { setCsrfToken, setUnauthorizedHandler } from '../api/client';
import { AuthContext, LOCAL_ACCESS, serverAccess, type AuthValue } from '../auth/context';
import { LoginPage } from '../pages/LoginPage';

/**
 * Asks the backend which edition it runs (GET /edition) before showing the app.
 *
 * - Local edition (or the question fails, e.g. the backend is down or older):
 *   the app is shown exactly as before — no login, nothing new in the UI.
 * - Server edition: GET /auth/me decides between the login page and the app.
 *   A 401 from any later request (session expired, logged out in another
 *   tab) returns to the login page; the app, and with it the live-sync
 *   WebSocket, is unmounted then and connects again after the login (a full
 *   page load back from the identity provider).
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const edition = useEdition();
  const serverMode = edition.data?.mode === 'server';
  const session = useAuthSession(serverMode);

  useEffect(() => {
    if (!serverMode) return undefined;
    setUnauthorizedHandler(() => {
      setCsrfToken(null);
      queryClient.setQueryData(authKeys.session, null);
    });
    return () => setUnauthorizedHandler(null);
  }, [serverMode, queryClient]);

  const logout = useCallback(async () => {
    await logoutRequest();
    setCsrfToken(null);
    // A fresh page: no cached data of this user, no open socket.
    window.location.assign('/');
  }, []);

  const user = session.data?.user ?? null;
  const permissions = session.data?.permissions;
  const value = useMemo<AuthValue>(
    () =>
      serverMode
        ? {
            mode: 'server',
            user,
            canManageServer: user?.server_admin ?? false,
            access: serverAccess(permissions, user?.server_admin ?? false),
            logout,
          }
        : { mode: 'local', user: null, canManageServer: true, access: LOCAL_ACCESS, logout: async () => undefined },
    [serverMode, user, permissions, logout],
  );

  if (edition.isPending || (serverMode && session.isPending)) {
    return (
      <Center h="100vh">
        <Loader aria-label="Loading" />
      </Center>
    );
  }
  if (serverMode && !user) {
    return (
      <LoginPage
        providers={edition.data?.providers ?? []}
        error={session.isError ? session.error : null}
        onRetry={() => session.refetch()}
      />
    );
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
