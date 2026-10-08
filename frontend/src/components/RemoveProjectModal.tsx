import { Alert, Button, Center, Group, List, Loader, Modal, Stack, Text, TextInput } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { subscribeDatasetCleared, useConfig, useProjectRemovalInfo, useRemoveProject } from '../api/queries';

interface RemoveProjectModalProps {
  projectId: string;
  projectName: string;
  /** The data folder the dialog was opened in (null: unknown). */
  dataDir: string | null;
  onClose: () => void;
}

/**
 * "Remove project…": deletes the project folder after the user types the
 * project name. The server explains what blocks the removal (lock,
 * subprojects, projects referencing it) and how the project could be
 * recovered (its git history lives in the folder and goes with it; a remote
 * or an enclosing repository may keep a copy).
 *
 * Project ids repeat across data folders, so the dialog is bound to the data
 * folder it was opened in: it closes when the folder is switched (here or by
 * another client), refuses to confirm when the current folder differs, and
 * sends that folder along so the server refuses too (409).
 */
export function RemoveProjectModal({ projectId, projectName, dataDir, onClose }: RemoveProjectModalProps) {
  const infoQuery = useProjectRemovalInfo(projectId);
  const currentDataDir = useConfig().data?.data_dir ?? null;
  const removeProject = useRemoveProject();
  const [typed, setTyped] = useState('');

  // A data folder switch closes the dialog: its project is not in the new folder.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });
  useEffect(() => subscribeDatasetCleared(() => onCloseRef.current()), []);

  const folderChanged = dataDir === null || (currentDataDir !== null && currentDataDir !== dataDir);
  const info = infoQuery.data;
  const nameMatches = typed.trim() === (info?.name ?? projectName).trim();
  const canConfirm = !!info?.can_remove && nameMatches && !folderChanged && !removeProject.isPending;

  const submit = () => {
    if (!canConfirm) return;
    removeProject.mutate(
      { projectId, confirmName: typed, dataDir },
      {
        onSuccess: (result) => {
          notifications.show({ message: `Project "${result.name}" removed`, color: 'green' });
          if (result.leftover) {
            notifications.show({
              message: `Some files could not be deleted and were left in ${result.leftover}`,
              color: 'yellow',
            });
          }
          onClose();
        },
      },
    );
  };

  let body;
  if (infoQuery.isLoading) {
    body = (
      <Center py="md">
        <Loader size="sm" />
      </Center>
    );
  } else if (infoQuery.isError || !info) {
    body = (
      <Alert color="red" p="xs">
        <Text size="sm">
          {infoQuery.error instanceof ApiError ? infoQuery.error.message : 'Failed to check the project'}
        </Text>
      </Alert>
    );
  } else if (!info.can_remove) {
    body = (
      <Alert color="yellow" title="The project cannot be removed" p="sm">
        <Stack gap={4}>
          {info.locked && <Text size="sm">It is locked: unlock it first.</Text>}
          {info.subprojects.length > 0 && (
            <>
              <Text size="sm">It has subprojects; move or remove them first:</Text>
              <List size="sm">
                {info.subprojects.map((p) => (
                  <List.Item key={p.id}>
                    {p.name} ({p.id})
                  </List.Item>
                ))}
              </List>
            </>
          )}
          {info.referenced_by.length > 0 && (
            <>
              <Text size="sm">Other projects reference it; remove those references first:</Text>
              <List size="sm">
                {info.referenced_by.map((p) => (
                  <List.Item key={p.id}>
                    {p.name} ({p.id})
                  </List.Item>
                ))}
              </List>
            </>
          )}
        </Stack>
      </Alert>
    );
  } else {
    body = (
      <Stack gap="sm">
        <Text size="sm">
          This deletes the folder of "{info.name}" with all its modules, requirements, links and views. Nothing
          outside the project folder is changed.
        </Text>
        <Alert color="orange" p="xs" title="Recovery">
          <Text size="sm">{info.recovery}</Text>
        </Alert>
        <TextInput
          label={`Type the project name "${info.name}" to confirm`}
          data-autofocus
          value={typed}
          onChange={(event) => setTyped(event.currentTarget.value)}
        />
      </Stack>
    );
  }

  return (
    <Modal opened onClose={onClose} title={`Remove project "${projectName}"`} centered>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Stack gap="sm">
          {folderChanged && (
            <Alert color="red" p="xs">
              <Text size="sm">
                The data folder changed since this dialog was opened (or is unknown); nothing can be removed here.
              </Text>
            </Alert>
          )}
          {body}
          {removeProject.isError && (
            <Alert color="red" p="xs">
              <Text size="sm">
                {removeProject.error instanceof ApiError ? removeProject.error.message : 'Failed to remove the project'}
              </Text>
            </Alert>
          )}
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" color="red" disabled={!canConfirm} loading={removeProject.isPending}>
              Remove project
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
