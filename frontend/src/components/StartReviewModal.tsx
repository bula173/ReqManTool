import { Button, Group, Modal, Stack, Text, TextInput } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useStartReview } from '../api/queries';
import type { ProviderKind } from '../api/types';

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}

/**
 * “Request review”: push `current` to a review branch and open a merge
 * request (GitLab) / pull request (GitHub) into `current`, with a generated
 * description (changed requirements, baseline reference).
 */
export function StartReviewModal({
  projectId,
  provider,
  onClose,
}: {
  projectId: string;
  provider: ProviderKind | null;
  onClose: () => void;
}) {
  const start = useStartReview(projectId);
  const [branch, setBranch] = useState('');
  const [title, setTitle] = useState('');
  const noun = provider === 'github' ? 'pull request' : provider === 'gitlab' ? 'merge request' : 'merge / pull request';

  const submit = () => {
    start.mutate(
      { branch: branch.trim() || null, title: title.trim() || null },
      {
        onSuccess: (result) => {
          notifications.show({
            message: `Opened ${noun} #${result.review.number} from ${result.pushed.branch}`,
            color: 'green',
          });
          onClose();
        },
      },
    );
  };

  return (
    <Modal opened onClose={onClose} title={`Request review (${noun})`} size="lg" centered>
      <Stack gap="sm">
        <Text size="sm">
          Pushes this project’s branch “current” to a review branch and opens a {noun} into “current” on the remote.
          The description lists the changed requirements and the latest baseline.
        </Text>
        <TextInput
          label="Review branch"
          description="Empty: review/<date-time>. An open request from the same branch is updated instead."
          placeholder="review/20261008-120000"
          value={branch}
          onChange={(event) => setBranch(event.currentTarget.value)}
          disabled={start.isPending}
        />
        <TextInput
          label="Title"
          description="Empty: “Review: <project> (<n> commits)”"
          value={title}
          onChange={(event) => setTitle(event.currentTarget.value)}
          disabled={start.isPending}
        />
        {start.isError && (
          <Text size="sm" c="red" style={{ wordBreak: 'break-word' }}>
            {errorText(start.error, 'Failed to open the review')}
          </Text>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={start.isPending}>
            Cancel
          </Button>
          <Button onClick={submit} loading={start.isPending}>
            Push and open {noun}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
