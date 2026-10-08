import { Badge, Tooltip } from '@mantine/core';
import { PROJECT_ROLE_LABELS, type ProjectRole } from '../api/types';
import { useAuth } from '../auth/context';

const ROLE_HINTS: Record<ProjectRole, string> = {
  viewer: 'You can read this project; changes are disabled.',
  reviewer: 'You can read this project and sign off requirements (change their status); other changes are disabled.',
  editor: 'You can change this project’s data.',
  'project-admin':
    'You can change and administer this project (baselines, lock, attributes, views, link configurations, approval, members, git settings).',
};

const ROLE_COLORS: Record<ProjectRole, string> = {
  viewer: 'gray',
  reviewer: 'teal',
  editor: 'blue',
  'project-admin': 'grape',
};

/** The user's role in a project (server edition), and where it comes from; nothing in the local edition. */
export function ProjectRoleBadge({ projectId }: { projectId: string | null | undefined }) {
  const { mode, access } = useAuth();
  if (mode !== 'server' || !projectId) return null;
  if (access.serverAdmin) {
    return (
      <Tooltip label="Server admins may do everything in every project" withArrow>
        <Badge size="sm" variant="light" color="grape">
          Server admin
        </Badge>
      </Tooltip>
    );
  }
  const role = access.role(projectId);
  const repo = access.repoRole(projectId);
  if (!role) {
    if (!repo?.error) return null;
    return (
      <Tooltip label={`Your access cannot be checked: ${repo.error}`} multiline w={320} withArrow>
        <Badge size="sm" variant="light" color="red">
          No access
        </Badge>
      </Tooltip>
    );
  }
  const source = repo
    ? ` From your ${repo.provider_name} permission: ${repo.permission ?? 'none'} on ${repo.repository}.`
    : '';
  const stale = repo?.error ? ` The last check failed (${repo.error}); this is the last known role.` : '';
  return (
    <Tooltip label={`${ROLE_HINTS[role]}${source}${stale}`} multiline w={320} withArrow>
      <Badge size="sm" variant="light" color={repo?.error ? 'orange' : ROLE_COLORS[role]}>
        {PROJECT_ROLE_LABELS[role]}
        {repo ? ` · ${repo.provider_name}` : ''}
      </Badge>
    </Tooltip>
  );
}
