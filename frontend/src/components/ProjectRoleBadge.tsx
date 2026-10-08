import { Badge, Tooltip } from '@mantine/core';
import { PROJECT_ROLE_LABELS } from '../api/types';
import { useAuth } from '../auth/context';

const ROLE_HINTS = {
  viewer: 'You can read this project; changes are disabled.',
  editor: 'You can change this project’s data.',
  'project-admin': 'You can change and administer this project (lock, remove, members, git settings).',
} as const;

/** The user's role in a project (server edition); nothing in the local edition. */
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
  if (!role) return null;
  return (
    <Tooltip label={ROLE_HINTS[role]} withArrow>
      <Badge size="sm" variant="light" color={role === 'viewer' ? 'gray' : role === 'editor' ? 'blue' : 'grape'}>
        {PROJECT_ROLE_LABELS[role]}
      </Badge>
    </Tooltip>
  );
}
