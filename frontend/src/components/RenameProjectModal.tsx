import { Alert, Button, Group, Modal, Stack, Text, TextInput } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useRenameProject } from '../api/queries';
import type { Project } from '../api/types';

/**
 * "Rename project…": changes the project's display name. Only the name
 * changes — the id (and so the folder, subproject/reference ids, cross-
 * project links and git remotes) stays. Refused while the project is
 * locked (423) and for a non-admin (server edition, 403).
 */
export function RenameProjectModal({ project, onClose }: { project: Project; onClose: () => void }) {
  const renameProject = useRenameProject();
  const [name, setName] = useState(project.name);

  const trimmed = name.trim();

  const submit = () => {
    renameProject.mutate(
      { projectId: project.id, name: trimmed },
      {
        onSuccess: () => {
          notifications.show({ message: `Project renamed to "${trimmed}"`, color: 'green' });
          onClose();
        },
      },
    );
  };

  return (
    <Modal opened onClose={onClose} title={`Rename project "${project.name}"`} centered>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Stack gap="sm">
          <Text size="sm">
            Only the name changes: the project's id, its folder and everything referring to it (subprojects,
            references, links, git remote) stay as they are.
          </Text>
          <TextInput
            label="Name"
            placeholder="My Project"
            required
            maxLength={200}
            data-autofocus
            value={name}
            onChange={(event) => setName(event.currentTarget.value)}
          />
          {renameProject.isError && (
            <Alert color="red" p="xs">
              <Text size="sm">
                {renameProject.error instanceof ApiError ? renameProject.error.message : 'Failed to rename the project'}
              </Text>
            </Alert>
          )}
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={renameProject.isPending} disabled={trimmed.length === 0 || trimmed === project.name}>
              Rename
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
