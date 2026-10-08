import { createContext, useContext } from 'react';
import type { ProjectRole, RepoRoleInfo, SessionUser, UserPermissions } from '../api/types';

/**
 * What the logged-in user may do (server edition), mirroring the backend's
 * permission guard so the UI can disable what the server would refuse (403).
 * In the local edition everything is allowed.
 */
export interface ProjectAccess {
  serverMode: boolean;
  serverAdmin: boolean;
  /** New projects (blank or from a template); the creator becomes their project admin. */
  canCreateProjects: boolean;
  /** Clone from a URL, GitLab/GitHub connections (server admins only in the server edition). */
  canUseConnections: boolean;
  /** Whether the admin pages have something for the user: a server admin, or a project admin somewhere. */
  canOpenAdministration: boolean;
  /** The user's role in a project; null without access. Server admins and the local edition: `project-admin`. */
  role: (projectId: string | null | undefined) => ProjectRole | null;
  /** Where the role comes from when it is a repository permission (server edition), else null. */
  repoRole: (projectId: string | null | undefined) => RepoRoleInfo | null;
  /** Sign off requirements: change only their status (reviewer or more). A null project id is allowed. */
  canReview: (projectId: string | null | undefined) => boolean;
  /** Change the project's data (editor or more). A null project id (no project) is allowed. */
  canEdit: (projectId: string | null | undefined) => boolean;
  /** Lock/unlock, remove, purge, parent, git settings, members (project admin). Null project id: allowed. */
  canAdmin: (projectId: string | null | undefined) => boolean;
}

export const LOCAL_ACCESS: ProjectAccess = {
  serverMode: false,
  serverAdmin: true,
  canCreateProjects: true,
  canUseConnections: true,
  canOpenAdministration: false,
  role: () => 'project-admin',
  repoRole: () => null,
  canReview: () => true,
  canEdit: () => true,
  canAdmin: () => true,
};

const RANK: Record<ProjectRole, number> = { viewer: 1, reviewer: 2, editor: 3, 'project-admin': 4 };

/** The server edition's access rules for a user's permissions (from GET /auth/me). */
export function serverAccess(permissions: UserPermissions | undefined, serverAdmin: boolean): ProjectAccess {
  const admin = serverAdmin || !!permissions?.server_admin;
  const role = (projectId: string | null | undefined): ProjectRole | null => {
    if (admin) return 'project-admin';
    if (!projectId || !permissions) return null;
    // A project with an entry (a membership, a group rule, a repository permission — even none) ignores default_role.
    if (Object.prototype.hasOwnProperty.call(permissions.roles, projectId)) return permissions.roles[projectId] ?? null;
    return permissions.default_role ?? null;
  };
  const atLeast = (projectId: string | null | undefined, needed: ProjectRole) => {
    if (!projectId) return true;
    const r = role(projectId);
    return r !== null && RANK[r] >= RANK[needed];
  };
  return {
    serverMode: true,
    serverAdmin: admin,
    canCreateProjects: admin || !!permissions?.can_create_projects,
    canUseConnections: admin,
    canOpenAdministration:
      admin ||
      permissions?.default_role === 'project-admin' ||
      Object.values(permissions?.roles ?? {}).includes('project-admin'),
    role,
    repoRole: (projectId) => (projectId ? (permissions?.repo_roles?.[projectId] ?? null) : null),
    canReview: (projectId) => atLeast(projectId, 'reviewer'),
    canEdit: (projectId) => atLeast(projectId, 'editor'),
    canAdmin: (projectId) => atLeast(projectId, 'project-admin'),
  };
}

export interface AuthValue {
  /** `local`: no login (the community edition); `server`: a logged-in user. */
  mode: 'local' | 'server';
  /** The logged-in user (server edition), else null. */
  user: SessionUser | null;
  /** Server-wide settings (data folder): always in local mode, server admins only in server mode. */
  canManageServer: boolean;
  /** What the user may do in which project. */
  access: ProjectAccess;
  /** Ends the session and returns to the login page (server edition). */
  logout: () => Promise<void>;
}

const LOCAL_AUTH: AuthValue = {
  mode: 'local',
  user: null,
  canManageServer: true,
  access: LOCAL_ACCESS,
  logout: async () => undefined,
};

export const AuthContext = createContext<AuthValue>(LOCAL_AUTH);

/** The edition and logged-in user; local defaults outside `AuthGate`. */
export function useAuth(): AuthValue {
  return useContext(AuthContext);
}
