import { Alert, Button, Group, Modal, Stack, Text, TextInput } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useLockProject } from '../api/queries';
import type { Project } from '../api/types';

/**
 * "Lock project…": asks for an optional reason and locks the project. While
 * it is locked the server refuses every change of it (its modules, folders,
 * requirements, links, views, attributes, references, row styles, imports)
 * for every client, until someone unlocks it. Who and when come from the
 * server (the git identity commits use).
 */
export function LockProjectModal({ project, onClose }: { project: Project; onClose: () => void }) {
  const lockProject = useLockProject();
  const [reason, setReason] = useState('');

  const submit = () => {
    lockProject.mutate(
      { projectId: project.id, reason: reason.trim() },
      {
        onSuccess: () => {
          notifications.show({ message: `Project "${project.name}" locked`, color: 'green' });
          onClose();
        },
      },
    );
  };

  return (
    <Modal opened onClose={onClose} title={`Lock project "${project.name}"`} centered>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Stack gap="sm">
          <Text size="sm">
            While the project is locked, every change of it — modules, folders, requirements, links, views,
            attributes, references, row styles and imports — is refused for everyone until it is unlocked.
            Baselines, exports, reports and git sync stay available.
          </Text>
          <TextInput
            label="Reason"
            placeholder="e.g. Released as v1.0"
            maxLength={500}
            data-autofocus
            value={reason}
            onChange={(event) => setReason(event.currentTarget.value)}
          />
          {lockProject.isError && (
            <Alert color="red" p="xs">
              <Text size="sm">
                {lockProject.error instanceof ApiError ? lockProject.error.message : 'Failed to lock the project'}
              </Text>
            </Alert>
          )}
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={lockProject.isPending}>
              Lock project
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
