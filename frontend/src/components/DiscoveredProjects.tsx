import { ActionIcon, Button, Group, Loader, Stack, Text, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconBrandGithub, IconBrandGitlab, IconLock, IconRefresh } from '@tabler/icons-react';
import { useOpenDiscoveredProject, useDiscoveredProjects, useRefreshDiscovery } from '../api/discovery';
import type { DiscoveredRepository } from '../api/types';
import { useAuth } from '../auth/context';

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/**
 * The Workspace tab's "Available on GitHub/GitLab" section (server edition,
 * logged in through GitHub or GitLab): the user's repositories that hold a
 * ReqManTool project and are not in the data folder yet. "Open" has the
 * server clone the repository (once, for everyone); it then shows as a
 * normal project above. Hidden when the server answers that discovery does
 * not apply to this user.
 */
export function DiscoveredProjects({ onPickProject }: { onPickProject: (projectId: string) => void }) {
  const { mode, user } = useAuth();
  const query = useDiscoveredProjects(mode === 'server' && !!user);
  const refresh = useRefreshDiscovery();
  const open = useOpenDiscoveredProject();

  if (mode !== 'server' || !user) return null;
  const data = query.data;
  if (data && !data.enabled) return null;

  const providerName = data?.enabled ? data.provider.name : 'GitHub/GitLab';
  const scanning = !!data?.enabled && data.status === 'scanning';
  const available = data?.enabled ? data.repositories.filter((r) => !r.already_opened) : [];
  const ProviderIcon = data?.enabled && data.provider.type === 'gitlab' ? IconBrandGitlab : IconBrandGithub;

  const openRepository = (repository: DiscoveredRepository) => {
    open.mutate(
      { provider: repository.provider_id, repo: repository.full_name },
      {
        onSuccess: (result) => {
          notifications.show({
            message: result.cloned
              ? `Opened ${result.repository} as project "${result.project_id}"`
              : `${result.repository} is already open as project "${result.project_id}"`,
            color: 'green',
          });
          for (const note of result.notes) notifications.show({ message: note, color: 'yellow' });
          onPickProject(result.project_id);
        },
      },
    );
  };

  return (
    <Stack gap={4} px="xs" pt="sm" pb="xs" aria-label={`Available on ${providerName}`} component="section">
      <Group justify="space-between" wrap="nowrap" gap={4}>
        <Group gap={6} wrap="nowrap">
          <ProviderIcon size={16} />
          <Text size="sm" fw={600}>
            Available on {providerName}
          </Text>
        </Group>
        <Tooltip label={scanning ? 'Scanning your repositories…' : 'Scan your repositories again'}>
          <ActionIcon
            variant="subtle"
            size="sm"
            aria-label="Scan repositories again"
            onClick={() => refresh.mutate()}
            loading={scanning || refresh.isPending}
            disabled={query.isLoading}
          >
            <IconRefresh size={14} />
          </ActionIcon>
        </Tooltip>
      </Group>
      {query.isLoading && (
        <Group gap={6} px={4}>
          <Loader size="xs" />
          <Text size="xs" c="dimmed">
            Looking for projects…
          </Text>
        </Group>
      )}
      {query.isError && (
        <Text c="red" size="xs" px={4}>
          {errorText(query.error, 'The repositories could not be listed')}
        </Text>
      )}
      {refresh.isError && (
        <Text c="red" size="xs" px={4}>
          {errorText(refresh.error, 'The scan could not be started')}
        </Text>
      )}
      {data?.enabled && data.error && (
        <Text c="red" size="xs" px={4}>
          {data.error}
        </Text>
      )}
      {data?.enabled && scanning && available.length === 0 && (
        <Text size="xs" c="dimmed" px={4}>
          Scanning your repositories…
        </Text>
      )}
      {data?.enabled && !scanning && !data.error && available.length === 0 && (
        <Text size="xs" c="dimmed" px={4}>
          {data.repositories.length > 0
            ? 'Every ReqManTool project of your repositories is open.'
            : 'No ReqManTool projects found in your repositories.'}
        </Text>
      )}
      {available.map((repository) => {
        const pending = open.isPending && open.variables?.repo === repository.full_name;
        return (
          <Group key={repository.full_name} justify="space-between" wrap="nowrap" gap={4} px={4}>
            <Stack gap={0} style={{ minWidth: 0 }}>
              <Group gap={4} wrap="nowrap">
                {repository.private && <IconLock size={12} aria-label="Private repository" />}
                <Text size="sm" truncate title={repository.project_name ?? repository.full_name}>
                  {repository.project_name ?? repository.full_name}
                </Text>
              </Group>
              <Text size="xs" c="dimmed" truncate title={repository.web_url ?? repository.full_name}>
                {repository.full_name}
              </Text>
              {repository.project_error && (
                <Text size="xs" c="orange" lineClamp={2}>
                  {repository.project_error}
                </Text>
              )}
            </Stack>
            <Button
              size="compact-xs"
              variant="light"
              onClick={() => openRepository(repository)}
              loading={pending}
              disabled={!!repository.project_error || (open.isPending && !pending)}
            >
              Open
            </Button>
          </Group>
        );
      })}
      {open.isError && (
        <Text c="red" size="xs" px={4}>
          {errorText(open.error, 'The repository could not be opened')}
        </Text>
      )}
      {data?.enabled && data.truncated && (
        <Text size="xs" c="dimmed" px={4}>
          Not every repository was looked at (at most {data.max_repos} per scan).
        </Text>
      )}
      {data?.enabled && data.warnings.length > 0 && (
        <Tooltip label={data.warnings.join('\n')} multiline w={360} style={{ whiteSpace: 'pre-line' }}>
          <Text size="xs" c="dimmed" px={4}>
            {data.warnings.length} repositor{data.warnings.length === 1 ? 'y' : 'ies'} could not be read.
          </Text>
        </Tooltip>
      )}
    </Stack>
  );
}
