import { Alert, Badge, Button, Card, Group, Loader, Stack, Text, Title, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconHistory } from '@tabler/icons-react';
import { useIsMutating } from '@tanstack/react-query';
import { useAppActions } from '../actions/context';
import { ApiError } from '../api/client';
import { queryKeys, useGitStatus } from '../api/queries';
import type { GitState } from '../api/types';

const STATE_LABELS: Record<GitState, { label: string; color: string }> = {
  ready: { label: 'Recording', color: 'green' },
  paused: { label: 'Paused', color: 'yellow' },
  no_repo: { label: 'Not enabled', color: 'gray' },
  nested: { label: 'Unavailable: nested repository', color: 'orange' },
  foreign: { label: 'Unavailable: .git points elsewhere', color: 'orange' },
  git_missing: { label: 'Unavailable: git missing', color: 'orange' },
  off: { label: 'Off', color: 'gray' },
  error: { label: 'Error', color: 'red' },
};

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/**
 * The project page's history (git) status line: state, last commit, pending
 * changes, warnings (git missing, nested repository, .git pointing to another
 * repository, paused, no commits yet, last auto-commit error) and the
 * "Enable history (git)" action. The full Git panel
 * (baselines, recent commits) is a later slice.
 */
export function GitStatusPanel({ projectId }: { projectId: string }) {
  const statusQuery = useGitStatus(projectId);
  const { run, isAvailable } = useAppActions();
  const enabling = useIsMutating({ mutationKey: queryKeys.enableHistory(projectId) }) > 0;
  const status = statusQuery.data;

  return (
    <Card withBorder>
      <Group justify="space-between" mb={status && status.state !== 'ready' ? 'sm' : 0} wrap="nowrap">
        <Group gap="xs" wrap="nowrap">
          <IconHistory size={18} />
          <Title order={4}>History (git)</Title>
          {status && (
            <Badge variant="light" color={STATE_LABELS[status.state].color}>
              {STATE_LABELS[status.state].label}
            </Badge>
          )}
          {status && status.pending_changes > 0 && (
            <Tooltip label="Data files that differ from the last commit" withArrow>
              <Badge variant="outline" color="yellow">
                {status.pending_changes} uncommitted
              </Badge>
            </Tooltip>
          )}
        </Group>
        {status?.can_enable && (
          <Button
            size="xs"
            leftSection={<IconHistory size={14} />}
            disabled={!isAvailable('project.enableHistory') || enabling}
            loading={enabling}
            onClick={() => run('project.enableHistory')}
          >
            Enable history (git)
          </Button>
        )}
      </Group>

      {statusQuery.isLoading && <Loader size="xs" mt="xs" />}
      {statusQuery.isError && (
        <Text size="sm" c="red" mt="xs">
          Failed to load the git status:{' '}
          {statusQuery.error instanceof ApiError || statusQuery.error instanceof Error
            ? statusQuery.error.message
            : 'Unknown error'}
        </Text>
      )}

      {status && (
        <Stack gap="xs" mt={status.state === 'ready' ? 'xs' : 0}>
          {status.state === 'ready' && !status.head && (
            <Text size="sm">
              No commits yet on branch <b>{status.branch}</b>: the project's files are not recorded until the first
              commit{status.can_enable ? ' (use “Enable history (git)”, or make any change)' : ''}.
            </Text>
          )}
          {status.state === 'ready' && status.head && (
            <Text size="sm">
              Every change is committed on branch <b>{status.branch}</b>
              {status.squash_window_minutes > 0
                ? ` (changes by one author within ${status.squash_window_minutes} min are squashed into one commit)`
                : ''}
              {status.author ? `, as ${status.author.name} <${status.author.email}>` : ''}.
            </Text>
          )}
          {status.head && (
            <Text size="sm" c="dimmed">
              Last commit {status.head.commit.slice(0, 8)}: {status.head.subject} — {status.head.author_name},{' '}
              {formatDate(status.head.date)}
            </Text>
          )}
          {status.message && status.state !== 'ready' && (
            <Alert
              color={STATE_LABELS[status.state].color}
              icon={status.state === 'no_repo' || status.state === 'off' ? undefined : <IconAlertTriangle size={16} />}
              p="xs"
            >
              <Text size="sm">{status.message}</Text>
            </Alert>
          )}
          {status.last_error && (
            <Alert color="red" title="Last auto-commit failed" icon={<IconAlertTriangle size={16} />} p="xs">
              <Text size="sm" style={{ wordBreak: 'break-word' }}>
                {status.last_error}
              </Text>
            </Alert>
          )}
        </Stack>
      )}
    </Card>
  );
}
