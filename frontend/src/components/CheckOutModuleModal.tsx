import { Alert, Button, Group, Modal, Stack, Text, TextInput } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useCheckOutModule } from '../api/moduleLocks';
import type { Module } from '../api/types';

/**
 * "Check out for editing…" (server edition): takes the module's exclusive
 * edit lock with the name of the editing session. Until it is released,
 * broken by a project admin or expires without changes, everybody else gets
 * the module read-only (the server refuses their changes with 423). The
 * name is required and labels the commits the session produces:
 * `[module][username][name]`.
 */
export function CheckOutModuleModal({
  projectId,
  module,
  onClose,
}: {
  projectId: string;
  module: Module;
  onClose: () => void;
}) {
  const checkOut = useCheckOutModule();
  const [name, setName] = useState('');
  const title = name.trim();
  const submit = () => {
    checkOut.mutate(
      { projectId, moduleId: module.id, reason: title },
      {
        onSuccess: () => {
          notifications.show({ message: `Module "${module.name}" checked out for editing`, color: 'green' });
          onClose();
        },
      },
    );
  };
  return (
    <Modal opened onClose={onClose} title={`Check out "${module.name}" for editing`} centered>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (title) submit();
        }}
      >
        <Stack gap="sm">
          <Text size="sm">
            While you have the module checked out, only you can change its requirements, its links and import into
            it; everybody else sees it read-only. Release it when you are done. It is released automatically after
            a while without changes, and a project admin can break the lock.
          </Text>
          <TextInput
            label="Name"
            description="Names your editing session and labels its commits: [module][username][name]"
            placeholder="e.g. Reworking the braking chapter"
            required
            maxLength={500}
            data-autofocus
            value={name}
            onChange={(event) => setName(event.currentTarget.value)}
          />
          {checkOut.isError && (
            <Alert color="red" p="xs">
              <Text size="sm">
                {checkOut.error instanceof ApiError ? checkOut.error.message : 'Failed to check out the module'}
              </Text>
            </Alert>
          )}
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={checkOut.isPending} disabled={!title}>
              Check out
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
