import { createContext, useContext } from 'react';
import type { SessionUser } from '../api/types';

export interface AuthValue {
  /** `local`: no login (the community edition); `server`: a logged-in user. */
  mode: 'local' | 'server';
  /** The logged-in user (server edition), else null. */
  user: SessionUser | null;
  /** Server-wide settings (data folder): always in local mode, server admins only in server mode. */
  canManageServer: boolean;
  /** Ends the session and returns to the login page (server edition). */
  logout: () => Promise<void>;
}

const LOCAL_AUTH: AuthValue = {
  mode: 'local',
  user: null,
  canManageServer: true,
  logout: async () => undefined,
};

export const AuthContext = createContext<AuthValue>(LOCAL_AUTH);

/** The edition and logged-in user; local defaults outside `AuthGate`. */
export function useAuth(): AuthValue {
  return useContext(AuthContext);
}
