import { Alert, Anchor, Badge, Button, Center, Group, Loader, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconTag } from '@tabler/icons-react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useCommits } from '../api/queries';
import type { CommitListEntry, CommitQuery } from '../api/types';
import { commitPath, formatCommitDate } from './commits';

function CommitRow({ projectId, entry }: { projectId: string; entry: CommitListEntry }) {
  return (
    <Table.Tr>
      <Table.Td>
        <Anchor component={Link} to={commitPath(projectId, entry.commit)} ff="monospace" size="sm">
          {entry.commit.slice(0, 8)}
        </Anchor>
      </Table.Td>
      <Table.Td>
        <Text size="sm" style={{ whiteSpace: 'nowrap' }}>
          {formatCommitDate(entry.date)}
        </Text>
      </Table.Td>
      <Table.Td>
        <Tooltip label={entry.author_email} withArrow openDelay={300} disabled={!entry.author_email}>
          <Text size="sm" style={{ width: 'fit-content' }}>
            {entry.author_name}
          </Text>
        </Tooltip>
      </Table.Td>
      <Table.Td>
        <Group gap={6} wrap="nowrap">
          {entry.baselines.map((name) => (
            <Badge key={name} size="xs" variant="light" color="grape" leftSection={<IconTag size={10} />}>
              {name}
            </Badge>
          ))}
          <Anchor component={Link} to={commitPath(projectId, entry.commit)} size="sm" c="inherit" lineClamp={1} title={entry.subject}>
            {entry.subject || '(no message)'}
          </Anchor>
        </Group>
      </Table.Td>
      <Table.Td ta="right">
        <Text size="sm" c={entry.changed_requirements ? undefined : 'dimmed'}>
          {entry.changed_requirements}
        </Text>
      </Table.Td>
    </Table.Tr>
  );
}

/**
 * The commit list of the commit browser, at project, module or requirement
 * scope (`query`), newest first, with "Load more". Each row links to the
 * commit's details. Read-only; refetched by live sync.
 */
export function CommitList({ projectId, query }: { projectId: string; query: CommitQuery }) {
  const commitsQuery = useCommits(projectId, query);
  const pages = commitsQuery.data?.pages ?? [];
  const first = pages[0];
  const entries = pages.flatMap((page) => page.entries);
  const filtered = !!(query.author || query.text || query.since || query.until);

  if (commitsQuery.isLoading) {
    return (
      <Center py="md">
        <Loader size="sm" />
      </Center>
    );
  }
  // A failed first page (a failed "Load more" keeps the loaded pages; see below).
  if (commitsQuery.isError && !commitsQuery.data) {
    return (
      <Alert color="red" title="Failed to load the commits">
        {commitsQuery.error instanceof ApiError || commitsQuery.error instanceof Error
          ? commitsQuery.error.message
          : 'Unknown error'}
      </Alert>
    );
  }
  if (first && !first.available) {
    return (
      <Alert color="gray" title="History not available">
        {first.reason ?? 'This project has no readable git history.'}
      </Alert>
    );
  }
  if (entries.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        {filtered ? 'No commits match the filters.' : 'No commits yet.'}
      </Text>
    );
  }
  return (
    <Stack gap="sm">
      <div className="reqman-table-scroll">
        <Table striped highlightOnHover stickyHeader>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={100}>Commit</Table.Th>
              <Table.Th w={190}>Date</Table.Th>
              <Table.Th w={180}>Author</Table.Th>
              <Table.Th>Message</Table.Th>
              <Table.Th w={110} ta="right">
                <Tooltip label="Requirements whose files the commit changed" withArrow>
                  <span>Requirements</span>
                </Tooltip>
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {entries.map((entry) => (
              <CommitRow key={entry.commit} projectId={projectId} entry={entry} />
            ))}
          </Table.Tbody>
        </Table>
      </div>
      {commitsQuery.hasNextPage && (
        <Group justify="center">
          <Button
            variant="default"
            size="xs"
            loading={commitsQuery.isFetchingNextPage}
            onClick={() => void commitsQuery.fetchNextPage()}
          >
            Load more
          </Button>
        </Group>
      )}
      {commitsQuery.isError && commitsQuery.isFetchNextPageError && (
        <Text size="sm" c="red">
          Failed to load more commits:{' '}
          {commitsQuery.error instanceof Error ? commitsQuery.error.message : 'Unknown error'}
        </Text>
      )}
    </Stack>
  );
}
