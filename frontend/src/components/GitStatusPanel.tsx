import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconCamera,
  IconCloudDownload,
  IconCloudUpload,
  IconGitCompare,
  IconHistory,
} from '@tabler/icons-react';
import { useIsMutating } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAppActions } from '../actions/context';
import { ApiError } from '../api/client';
import {
  queryKeys,
  useBaselines,
  useGitLog,
  useGitPull,
  useGitPush,
  useGitStatus,
  useSetGitAutoPush,
  useSetGitRemote,
} from '../api/queries';
import type { GitRemoteStatus, GitState } from '../api/types';
import { CreateBaselineModal } from './CreateBaselineModal';

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

const RECENT_COMMITS = 10;

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}

/** Recent commits of the `current` branch (collapsed into the Git panel). */
function RecentCommits({ projectId }: { projectId: string }) {
  const logQuery = useGitLog(projectId, RECENT_COMMITS);

  if (logQuery.isLoading) return <Loader size="xs" />;
  if (logQuery.isError) {
    return (
      <Text size="sm" c="red">
        Failed to load the commits: {errorMessage(logQuery.error, 'Unknown error')}
      </Text>
    );
  }
  const log = logQuery.data;
  if (!log || !log.available) return null;
  if (log.entries.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No commits yet.
      </Text>
    );
  }
  return (
    <Stack gap={2}>
      {log.entries.map((entry) => (
        <Text size="sm" key={entry.commit} lineClamp={1}>
          <Text span c="dimmed" ff="monospace">
            {entry.commit.slice(0, 8)}
          </Text>{' '}
          {entry.subject}{' '}
          <Text span c="dimmed" size="xs">
            — {entry.author_name}, {formatDate(entry.date)}
          </Text>
        </Text>
      ))}
      {log.truncated && (
        <Text size="xs" c="dimmed">
          Older commits are not shown (git log in the project folder lists them all).
        </Text>
      )}
    </Stack>
  );
}

/**
 * The project's remote: URL (set / change / remove, validated server-side
 * with `git ls-remote`), explicit Push and Pull, ahead/behind, the auto-push
 * opt-in and the sync-conflict warning. The remote is an SSH/HTTPS URL or a
 * local folder (absolute path or file:// URL, e.g. a network share).
 * Credentials stay with the user's git
 * (ssh agent / credential helper); ReqManTool never stores them.
 */
function RemoteSync({ projectId, remote }: { projectId: string; remote: GitRemoteStatus }) {
  const setRemote = useSetGitRemote(projectId);
  const setAutoPush = useSetGitAutoPush(projectId);
  const push = useGitPush(projectId);
  const pull = useGitPull(projectId);
  const [editing, setEditing] = useState(false);
  const [url, setUrl] = useState(remote.url ?? '');
  // Derived, not snapshotted: a live-sync removal of the remote must show the
  // URL form, not stale Push/Pull controls.
  const showForm = editing || remote.url === null;
  const busy = setRemote.isPending || setAutoPush.isPending || push.isPending || pull.isPending;

  const save = () => {
    setRemote.mutate(url.trim() || null, { onSuccess: () => setEditing(false) });
  };

  return (
    <Stack gap="xs">
      <Title order={5}>Remote</Title>
      {remote.sync_conflict && (
        <Alert color="red" title="Sync conflict" icon={<IconAlertTriangle size={16} />} p="xs">
          <Text size="sm" style={{ wordBreak: 'break-word' }}>
            {remote.conflict_message ??
              'The local history and the remote’s have diverged; changes are blocked until they are reconciled in git.'}
          </Text>
        </Alert>
      )}
      {showForm ? (
        <Group gap="xs" align="flex-end" wrap="nowrap">
          <TextInput
            label="Remote URL"
            description="SSH, HTTPS or a local folder / network share (absolute path or file:// URL); credentials stay with your git setup (ssh agent / credential helper)"
            placeholder="git@example.com:team/project.git or /Volumes/nas/project.git"
            value={url}
            onChange={(event) => setUrl(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') save();
            }}
            style={{ flex: 1 }}
            disabled={busy}
          />
          <Button size="xs" onClick={save} loading={setRemote.isPending} disabled={busy && !setRemote.isPending}>
            Save
          </Button>
          {remote.url !== null && (
            <Button
              size="xs"
              variant="default"
              disabled={busy}
              onClick={() => {
                setUrl(remote.url ?? '');
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          )}
        </Group>
      ) : (
        <Group gap="xs" wrap="nowrap">
          <Text size="sm" ff="monospace" lineClamp={1} style={{ flex: 1, wordBreak: 'break-all' }}>
            {remote.url}
          </Text>
          {remote.ahead !== null && remote.behind !== null && (
            <Tooltip label="Commits to push / fetched commits to pull (since the last push or pull)" withArrow>
              <Badge variant="outline" color={remote.sync_conflict ? 'red' : 'gray'}>
                ↑{remote.ahead} ↓{remote.behind}
              </Badge>
            </Tooltip>
          )}
          <Button
            size="xs"
            variant="default"
            leftSection={<IconCloudUpload size={14} />}
            loading={push.isPending}
            disabled={busy && !push.isPending}
            onClick={() => push.mutate()}
          >
            Push
          </Button>
          <Button
            size="xs"
            variant="default"
            leftSection={<IconCloudDownload size={14} />}
            loading={pull.isPending}
            disabled={busy && !pull.isPending}
            onClick={() => pull.mutate()}
          >
            Pull
          </Button>
          <Button size="xs" variant="subtle" disabled={busy} onClick={() => setEditing(true)}>
            Change…
          </Button>
        </Group>
      )}
      {remote.url !== null && (
        <Switch
          size="xs"
          label="Push automatically after each change and baseline"
          checked={remote.auto_push}
          disabled={busy}
          onChange={(event) => setAutoPush.mutate(event.currentTarget.checked)}
        />
      )}
      {setRemote.isError && (
        <Text size="sm" c="red">
          {errorMessage(setRemote.error, 'Failed to set the remote')}
        </Text>
      )}
      {setAutoPush.isError && (
        <Text size="sm" c="red">
          {errorMessage(setAutoPush.error, 'Failed to change auto-push')}
        </Text>
      )}
      {push.isError && (
        <Text size="sm" c="red">
          Push failed: {errorMessage(push.error, 'Unknown error')}
        </Text>
      )}
      {push.isSuccess && !push.isPending && (
        <Text size="sm" c="green">
          Pushed {push.data.pushed.length} ref{push.data.pushed.length === 1 ? '' : 's'}.
        </Text>
      )}
      {pull.isError && (
        <Text size="sm" c="red">
          Pull failed: {errorMessage(pull.error, 'Unknown error')}
        </Text>
      )}
      {pull.isSuccess && !pull.isPending && (
        <Text size="sm" c={pull.data.updated ? 'green' : 'dimmed'}>
          {pull.data.updated ? 'Pulled: the project was updated.' : 'Already up to date.'}
          {pull.data.notes.length > 0 ? ` ${pull.data.notes.join(' ')}` : ''}
        </Text>
      )}
    </Stack>
  );
}

/** The baselines table: list (newest first), Create, Browse, Compare. */
function Baselines({ projectId, canCreate }: { projectId: string; canCreate: boolean }) {
  const baselinesQuery = useBaselines(projectId);
  const [createOpened, setCreateOpened] = useState(false);

  const list = baselinesQuery.data;
  return (
    <Stack gap="xs">
      <Group justify="space-between">
        <Group gap="xs">
          <Title order={5}>Baselines</Title>
          {list && list.baselines.length >= 2 && (
            <Button
              size="compact-xs"
              variant="default"
              leftSection={<IconGitCompare size={12} />}
              component={Link}
              to={`/projects/${projectId}/baselines/compare`}
            >
              Compare
            </Button>
          )}
        </Group>
        <Button
          size="xs"
          leftSection={<IconCamera size={14} />}
          disabled={!canCreate}
          onClick={() => setCreateOpened(true)}
        >
          Create baseline
        </Button>
      </Group>
      {baselinesQuery.isLoading && <Loader size="xs" />}
      {baselinesQuery.isError && (
        <Text size="sm" c="red">
          Failed to load the baselines: {errorMessage(baselinesQuery.error, 'Unknown error')}
        </Text>
      )}
      {list && list.available && list.baselines.length === 0 && (
        <Text size="sm" c="dimmed">
          No baselines yet. A baseline is a named, immutable snapshot of the project; two baselines (or a
          baseline and the current state) can be compared requirement by requirement.
        </Text>
      )}
      {list && list.baselines.length > 0 && (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Description</Table.Th>
              <Table.Th>Date</Table.Th>
              <Table.Th>Created by</Table.Th>
              <Table.Th w={180} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {list.baselines.map((baseline) => (
              <Table.Tr key={baseline.name}>
                <Table.Td>
                  <Group gap={6} wrap="nowrap">
                    <Text size="sm" fw={500}>
                      {baseline.name}
                    </Text>
                    {!baseline.annotated && (
                      <Tooltip label="A lightweight baseline/* tag made outside ReqManTool" withArrow>
                        <Badge size="xs" variant="outline" color="gray">
                          tag
                        </Badge>
                      </Tooltip>
                    )}
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" c={baseline.description ? undefined : 'dimmed'} lineClamp={2}>
                    {baseline.description || '—'}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{formatDate(baseline.date)}</Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{baseline.tagger ? baseline.tagger.name : '—'}</Text>
                </Table.Td>
                <Table.Td>
                  <Group gap="xs" justify="flex-end" wrap="nowrap">
                    <Anchor
                      component={Link}
                      size="sm"
                      to={`/projects/${projectId}/baselines/${encodeURIComponent(baseline.name)}`}
                    >
                      Browse
                    </Anchor>
                    <Anchor
                      component={Link}
                      size="sm"
                      to={`/projects/${projectId}/baselines/compare?from=${encodeURIComponent(baseline.name)}&to=current`}
                    >
                      Compare with current
                    </Anchor>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <CreateBaselineModal projectId={projectId} opened={createOpened} onClose={() => setCreateOpened(false)} />
    </Stack>
  );
}

/**
 * The project Git panel: state, branch, pending squash/session info, the
 * last commits, warnings, "Enable history (git)" and the baselines
 * (create / browse / compare).
 */
export function GitStatusPanel({ projectId }: { projectId: string }) {
  const statusQuery = useGitStatus(projectId);
  const { run, isAvailable } = useAppActions();
  const enabling = useIsMutating({ mutationKey: queryKeys.enableHistory(projectId) }) > 0;
  const status = statusQuery.data;
  const hasRepo = status?.state === 'ready' || status?.state === 'paused';

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
          {status?.branch && (
            <Badge variant="outline" color="gray">
              branch {status.branch}
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
          Failed to load the git status: {errorMessage(statusQuery.error, 'Unknown error')}
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
                ? ` (changes by one author within ${status.squash_window_minutes} min are squashed into one commit; creating a baseline closes that window)`
                : ''}
              {status.author ? `, as ${status.author.name} <${status.author.email}>` : ''}.
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
          {hasRepo && status.remote && <RemoteSync projectId={projectId} remote={status.remote} />}
          {hasRepo && status.head && (
            <>
              <Title order={5}>Last commits</Title>
              <RecentCommits projectId={projectId} />
            </>
          )}
          {hasRepo && <Baselines projectId={projectId} canCreate={status.state === 'ready'} />}
        </Stack>
      )}
    </Card>
  );
}
