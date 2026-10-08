import { Alert, Anchor, Badge, Button, Card, Center, Code, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { IconEye, IconGitCommit, IconInfoCircle, IconTag } from '@tabler/icons-react';
import { Link, useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useCommit, useProject } from '../api/queries';
import { commitBrowserPath, commitPath, formatCommitDate } from '../components/commits';
import { CompareResultView } from '../components/CompareResult';
import { compareAttributeNames } from '../components/compareNames';

/**
 * One commit of the commit browser: its message, author, parents and
 * baseline tags, and what it changed against its first parent at requirement
 * level (the baseline compare engine), plus notes on project/module file
 * changes such as module renames and reorders. "Browse project at this
 * commit" opens the read-only snapshot. Read-only.
 */
export function CommitDetailsPage() {
  const { projectId, commit } = useParams<{ projectId: string; commit: string }>();
  const commitQuery = useCommit(projectId, commit);
  const projectQuery = useProject(projectId);

  if (!projectId || !commit) return null;
  if (commitQuery.isLoading) {
    return (
      <Center h="60vh">
        <Loader />
      </Center>
    );
  }
  const details = commitQuery.data;
  if (commitQuery.isError || !details) {
    return (
      <Alert icon={<IconInfoCircle size={16} />} color="red" title={`Failed to load commit ${commit.slice(0, 8)}`}>
        {commitQuery.error instanceof ApiError || commitQuery.error instanceof Error
          ? commitQuery.error.message
          : 'Unknown error'}
      </Alert>
    );
  }

  const attributeName = compareAttributeNames(projectQuery.data?.attributes, details);
  const firstParent = details.parents[0] ?? null;
  const authorDiffers =
    details.committer_name !== details.author_name || details.committer_email !== details.author_email;

  return (
    <Stack gap="md">
      <div>
        <Group gap="xs">
          <IconGitCommit size={22} />
          <Title order={2}>
            Commit{' '}
            <Text span ff="monospace" inherit>
              {details.commit.slice(0, 8)}
            </Text>
          </Title>
          {details.baselines.map((name) => (
            <Badge key={name} variant="light" color="grape" leftSection={<IconTag size={12} />}>
              {name}
            </Badge>
          ))}
        </Group>
        <Text size="sm" c="dimmed">
          <Anchor component={Link} to={commitBrowserPath(projectId)} size="sm">
            All commits
          </Anchor>
          {' · '}
          <Anchor component={Link} to={`/projects/${projectId}`} size="sm">
            Back to the project
          </Anchor>
        </Text>
      </div>

      <Card withBorder>
        <Stack gap="xs">
          <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {details.message.trimEnd() || '(no message)'}
          </Code>
          <Text size="sm">
            <Text span c="dimmed">
              Author:{' '}
            </Text>
            {details.author_name} &lt;{details.author_email}&gt;, {formatCommitDate(details.authored_date)}
          </Text>
          {authorDiffers && (
            <Text size="sm">
              <Text span c="dimmed">
                Committer:{' '}
              </Text>
              {details.committer_name} &lt;{details.committer_email}&gt;, {formatCommitDate(details.date)}
            </Text>
          )}
          <Text size="sm">
            <Text span c="dimmed">
              Commit:{' '}
            </Text>
            <Text span ff="monospace">
              {details.commit}
            </Text>
          </Text>
          <Text size="sm">
            <Text span c="dimmed">
              {details.parents.length > 1 ? 'Parents: ' : 'Parent: '}
            </Text>
            {details.parents.length === 0 && 'none (the first commit)'}
            {details.parents.map((parent, index) => (
              <span key={parent}>
                {index > 0 && ', '}
                <Anchor component={Link} to={commitPath(projectId, parent)} ff="monospace" size="sm">
                  {parent.slice(0, 8)}
                </Anchor>
              </span>
            ))}
          </Text>
          <Group gap="sm" mt="xs">
            <Button
              component={Link}
              to={`/projects/${projectId}/commits/${details.commit}/browse`}
              variant="light"
              leftSection={<IconEye size={16} />}
            >
              Browse project at this commit
            </Button>
          </Group>
        </Stack>
      </Card>

      {details.notes.length > 0 && (
        <Card withBorder>
          <Title order={5} mb="xs">
            Project and module changes
          </Title>
          <Stack gap={2}>
            {details.notes.map((note, index) => (
              <Text size="sm" key={index}>
                {note}
              </Text>
            ))}
          </Stack>
        </Card>
      )}

      <CompareResultView
        result={details}
        attributeName={attributeName}
        extraUnchanged={details.notes.length === 0}
        noChangesText={
          details.changed_files === 0 ? 'This commit changed no project data files.' : 'No requirement-level changes.'
        }
        title={
          firstParent ? (
            <>
              Changes against{' '}
              <Text span ff="monospace" inherit>
                {firstParent.slice(0, 8)}
              </Text>
              {details.parents.length > 1 && ' (first parent)'}
            </>
          ) : (
            'Changes (first commit)'
          )
        }
      />
    </Stack>
  );
}
