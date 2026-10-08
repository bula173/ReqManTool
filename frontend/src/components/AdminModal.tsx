import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Center,
  Group,
  Loader,
  Modal,
  Select,
  Stack,
  Switch,
  Table,
  Tabs,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconRefresh, IconTrash, IconUserPlus } from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import {
  useAdminUsers,
  useAuditLog,
  useProjectMembers,
  useRemoveProjectMember,
  useSetProjectMember,
  useUpdateUser,
} from '../api/auth';
import { useProjects } from '../api/queries';
import { PROJECT_ROLES, PROJECT_ROLE_LABELS, isProjectStub, type AuditEntry, type ProjectRole } from '../api/types';
import { useAuth } from '../auth/context';

const ROLE_OPTIONS = PROJECT_ROLES.map((role) => ({ value: role, label: PROJECT_ROLE_LABELS[role] }));

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error';
}

function formatDate(iso: string): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/** Server admins: every user, with Active and Server admin switches (never for oneself). */
function UsersTab() {
  const { user: me } = useAuth();
  const usersQuery = useAdminUsers(true);
  const updateUser = useUpdateUser();

  const update = (userId: string, flags: { active?: boolean; server_admin?: boolean }) =>
    updateUser.mutate(
      { userId, ...flags },
      {
        onError: (error) => notifications.show({ color: 'red', title: 'Could not change the user', message: errorText(error) }),
      },
    );

  if (usersQuery.isLoading) {
    return (
      <Center p="md">
        <Loader size="sm" aria-label="Loading users" />
      </Center>
    );
  }
  if (usersQuery.isError) {
    return <Alert color="red">Failed to load the users: {errorText(usersQuery.error)}</Alert>;
  }
  const users = usersQuery.data?.users ?? [];
  if (users.length === 0) {
    return <Text c="dimmed">No users yet. Users appear here after their first login.</Text>;
  }
  return (
    <Stack gap="xs">
      <Text size="sm" c="dimmed">
        Users are created at their first login. A deactivated user cannot log in, and their sessions end at once.
      </Text>
      <Table striped withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>E-mail / user name</Table.Th>
            <Table.Th>Last login</Table.Th>
            <Table.Th>Active</Table.Th>
            <Table.Th>Server admin</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {users.map((u) => {
            const self = u.id === me?.id;
            return (
              <Table.Tr key={u.id}>
                <Table.Td>
                  <Text size="sm">{u.name}</Text>
                  {u.groups.length > 0 && (
                    <Tooltip label={u.groups.join(', ')} multiline w={320} withArrow>
                      <Text size="xs" c="dimmed">
                        {u.groups.length} group{u.groups.length === 1 ? '' : 's'}
                      </Text>
                    </Tooltip>
                  )}
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{u.email || '—'}</Text>
                  <Text size="xs" c="dimmed">
                    {u.username || u.id} ({u.provider})
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{formatDate(u.last_login_at)}</Text>
                </Table.Td>
                <Table.Td>
                  <Switch
                    aria-label={`${u.name} active`}
                    checked={u.active}
                    disabled={self || updateUser.isPending}
                    onChange={(event) => update(u.id, { active: event.currentTarget.checked })}
                  />
                </Table.Td>
                <Table.Td>
                  <Switch
                    aria-label={`${u.name} server admin`}
                    checked={u.server_admin}
                    disabled={self || updateUser.isPending}
                    onChange={(event) => update(u.id, { server_admin: event.currentTarget.checked })}
                  />
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

/** The members of one project: add by e-mail / user name, change role, remove. */
function MembersEditor({ projectId }: { projectId: string }) {
  const membersQuery = useProjectMembers(projectId);
  const setMember = useSetProjectMember(projectId);
  const removeMember = useRemoveProjectMember(projectId);
  const [newUser, setNewUser] = useState('');
  const [newRole, setNewRole] = useState<ProjectRole>('editor');
  const busy = setMember.isPending || removeMember.isPending;

  const fail = (title: string) => (error: unknown) => notifications.show({ color: 'red', title, message: errorText(error) });

  const add = () => {
    if (!newUser.trim()) return;
    setMember.mutate(
      { user: newUser.trim(), role: newRole },
      { onSuccess: () => setNewUser(''), onError: fail('Could not add the member') },
    );
  };

  if (membersQuery.isLoading) {
    return (
      <Center p="md">
        <Loader size="sm" aria-label="Loading members" />
      </Center>
    );
  }
  if (membersQuery.isError) {
    return <Alert color="red">Failed to load the members: {errorText(membersQuery.error)}</Alert>;
  }
  const members = membersQuery.data?.members ?? [];
  return (
    <Stack gap="sm">
      <Group align="flex-end" gap="xs" wrap="nowrap">
        <TextInput
          label="Add a user"
          description="E-mail address or user name (the user must have logged in once)"
          placeholder="jane@example.com"
          value={newUser}
          onChange={(event) => setNewUser(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') add();
          }}
          disabled={busy}
          style={{ flex: 1 }}
        />
        <Select
          label="Role"
          data={ROLE_OPTIONS}
          value={newRole}
          onChange={(value) => value && setNewRole(value as ProjectRole)}
          allowDeselect={false}
          disabled={busy}
          w={150}
        />
        <Button leftSection={<IconUserPlus size={16} />} onClick={add} loading={setMember.isPending} disabled={!newUser.trim() || busy}>
          Add
        </Button>
      </Group>
      {members.length === 0 ? (
        <Text c="dimmed" size="sm">
          No members yet: only server admins can open this project.
        </Text>
      ) : (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>User</Table.Th>
              <Table.Th>Role</Table.Th>
              <Table.Th aria-label="Remove" />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {members.map((m) => (
              <Table.Tr key={`${m.source}:${m.user_id}`}>
                <Table.Td>
                  <Group gap={6} wrap="nowrap">
                    <Text size="sm">{m.name}</Text>
                    {m.server_admin && (
                      <Badge size="xs" variant="light" color="grape">
                        Server admin
                      </Badge>
                    )}
                    {!m.active && (
                      <Badge size="xs" variant="light" color="gray">
                        Deactivated
                      </Badge>
                    )}
                  </Group>
                  <Text size="xs" c="dimmed">
                    {m.email || m.username || m.user_id}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {m.source === 'member' ? (
                    <Group gap={6} wrap="nowrap">
                      <Select
                        aria-label={`Role of ${m.name}`}
                        data={ROLE_OPTIONS}
                        value={m.role}
                        allowDeselect={false}
                        disabled={busy}
                        size="xs"
                        w={140}
                        onChange={(value) =>
                          value &&
                          value !== m.role &&
                          setMember.mutate(
                            { user: m.user_id, role: value as ProjectRole },
                            { onError: fail('Could not change the role') },
                          )
                        }
                      />
                      {m.group_role && (
                        <Text size="xs" c="dimmed">
                          group: {PROJECT_ROLE_LABELS[m.group_role]}
                        </Text>
                      )}
                    </Group>
                  ) : (
                    <Tooltip label="From a group rule in the server configuration (server.group_roles)" withArrow>
                      <Text size="sm">{PROJECT_ROLE_LABELS[m.role]} (group)</Text>
                    </Tooltip>
                  )}
                </Table.Td>
                <Table.Td>
                  {m.source === 'member' && (
                    <Tooltip label="Remove from the project" withArrow>
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        aria-label={`Remove ${m.name}`}
                        disabled={busy}
                        onClick={() => removeMember.mutate(m.user_id, { onError: fail('Could not remove the member') })}
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

/** Project admins (and server admins): pick one of the projects they administer, edit its members. */
function MembersTab({ initialProjectId }: { initialProjectId: string | null }) {
  const { access } = useAuth();
  const projectsQuery = useProjects();
  const options = useMemo(
    () =>
      (projectsQuery.data ?? [])
        .filter((p) => !isProjectStub(p) && access.canAdmin(p.id))
        .map((p) => ({ value: p.id, label: p.name }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [projectsQuery.data, access],
  );
  const [chosen, setChosen] = useState<string | null>(initialProjectId);
  // The chosen project while the user administers it, else the first one they do.
  const projectId = options.some((o) => o.value === chosen) ? chosen : (options[0]?.value ?? null);

  if (projectsQuery.isLoading) {
    return (
      <Center p="md">
        <Loader size="sm" aria-label="Loading projects" />
      </Center>
    );
  }
  if (projectsQuery.isError) {
    return <Alert color="red">Failed to load the projects: {errorText(projectsQuery.error)}</Alert>;
  }
  if (options.length === 0) {
    return <Text c="dimmed">You are not a project admin of any project.</Text>;
  }
  return (
    <Stack gap="md">
      <Select
        label="Project"
        data={options}
        value={projectId}
        onChange={setChosen}
        allowDeselect={false}
        searchable
      />
      {projectId && <MembersEditor projectId={projectId} />}
    </Stack>
  );
}

const AUDIT_FILTERS = [
  { value: '', label: 'All events' },
  { value: 'auth.', label: 'Logins and logouts' },
  { value: 'auth.login_failed', label: 'Failed logins' },
  { value: 'admin.', label: 'Users and members' },
  { value: 'project.', label: 'Projects' },
  { value: 'module.', label: 'Module locks' },
];

const AUDIT_ACTION_LABELS: Record<string, string> = {
  'auth.login': 'Logged in',
  'auth.login_failed': 'Login failed',
  'auth.logout': 'Logged out',
  'admin.user_update': 'User changed',
  'admin.member_set': 'Member role set',
  'admin.member_remove': 'Member removed',
  'module.lock_break': 'Module lock broken',
  'project.create': 'Project created',
  'project.lock': 'Project locked',
  'project.unlock': 'Project unlocked',
  'project.remove': 'Project removed',
  'project.purge': 'Project purged',
};

function auditValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return JSON.stringify(value);
}

/** What happened, in one line: the target user and the details. */
function auditSummary(entry: AuditEntry): string {
  const parts: string[] = [];
  if (entry.target_user_id) parts.push(`user ${entry.target_user_name || entry.target_user_id}`);
  for (const [key, value] of Object.entries(entry.details ?? {})) {
    parts.push(`${key.replace(/_/g, ' ')}: ${auditValue(value)}`);
  }
  return parts.join(' · ');
}

/** Server admins: the audit log (logins, admin changes, project lifecycle), newest first. */
function AuditTab() {
  const [filter, setFilter] = useState('');
  const audit = useAuditLog(filter, true);
  const entries = audit.data?.pages.flatMap((page) => page.entries) ?? [];
  return (
    <Stack gap="xs">
      <Group justify="space-between" align="flex-end">
        <Select
          label="Show"
          data={AUDIT_FILTERS}
          value={filter}
          onChange={(value) => setFilter(value ?? '')}
          allowDeselect={false}
          w={220}
        />
        <Button
          variant="default"
          leftSection={<IconRefresh size={16} />}
          onClick={() => audit.refetch()}
          loading={audit.isRefetching && !audit.isFetchingNextPage}
          disabled={audit.isLoading}
        >
          Refresh
        </Button>
      </Group>
      {audit.isLoading ? (
        <Center p="md">
          <Loader size="sm" aria-label="Loading the audit log" />
        </Center>
      ) : audit.isError ? (
        <Alert color="red">Failed to load the audit log: {errorText(audit.error)}</Alert>
      ) : entries.length === 0 ? (
        <Text c="dimmed">No events recorded{filter ? ' of this kind' : ''} yet.</Text>
      ) : (
        <>
          <Table striped withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Time</Table.Th>
                <Table.Th>Event</Table.Th>
                <Table.Th>By</Table.Th>
                <Table.Th>Project</Table.Th>
                <Table.Th>Details</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {entries.map((entry, index) => (
                <Table.Tr key={`${entry.time}-${index}`}>
                  <Table.Td>
                    <Text size="xs" style={{ whiteSpace: 'nowrap' }}>
                      {formatDate(entry.time)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Tooltip label={entry.action} withArrow openDelay={300}>
                      <Text size="sm" style={{ whiteSpace: 'nowrap' }}>
                        {AUDIT_ACTION_LABELS[entry.action] ?? entry.action}
                      </Text>
                    </Tooltip>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{entry.user_name || entry.user_id || '—'}</Text>
                    {entry.ip && (
                      <Text size="xs" c="dimmed">
                        {entry.ip}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{entry.project_id ?? '—'}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" style={{ wordBreak: 'break-word' }}>
                      {auditSummary(entry) || '—'}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {audit.hasNextPage && (
            <Center>
              <Button variant="subtle" onClick={() => audit.fetchNextPage()} loading={audit.isFetchingNextPage}>
                Load older events
              </Button>
            </Center>
          )}
        </>
      )}
    </Stack>
  );
}

/** Server edition administration: users and the audit log (server admins), project members (project admins). */
export function AdminModal({
  opened,
  onClose,
  initialProjectId = null,
}: {
  opened: boolean;
  onClose: () => void;
  initialProjectId?: string | null;
}) {
  const { access } = useAuth();
  const [tab, setTab] = useState<string | null>(access.serverAdmin ? 'users' : 'members');
  return (
    <Modal opened={opened} onClose={onClose} title="Administration" size="xl">
      {opened && (
        <Tabs value={tab} onChange={setTab} keepMounted={false}>
          <Tabs.List mb="md">
            {access.serverAdmin && <Tabs.Tab value="users">Users</Tabs.Tab>}
            <Tabs.Tab value="members">Project members</Tabs.Tab>
            {access.serverAdmin && <Tabs.Tab value="audit">Audit log</Tabs.Tab>}
          </Tabs.List>
          {access.serverAdmin && (
            <Tabs.Panel value="users">
              <UsersTab />
            </Tabs.Panel>
          )}
          <Tabs.Panel value="members">
            <MembersTab initialProjectId={initialProjectId} />
          </Tabs.Panel>
          {access.serverAdmin && (
            <Tabs.Panel value="audit">
              <AuditTab />
            </Tabs.Panel>
          )}
        </Tabs>
      )}
    </Modal>
  );
}
