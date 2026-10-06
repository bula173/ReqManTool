import { Button, Group, Modal, Stack, Text, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useAddProjectFromRemote } from '../api/queries';

/**
 * "Add project from remote": clones a git repository (SSH or HTTPS URL) into
 * the data directory and opens it as a project. The clone is validated
 * server-side like any loaded project; credentials stay with the user's git
 * setup (ssh agent / credential helper) and are never stored by ReqManTool.
 */
export function AddProjectFromRemoteModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  return (
    <Modal opened={opened} onClose={onClose} title="Add project from remote" centered>
      {opened && <AddProjectFromRemoteForm onClose={onClose} />}
    </Modal>
  );
}

function AddProjectFromRemoteForm({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const addProject = useAddProjectFromRemote();
  const form = useForm<{ url: string }>({
    initialValues: { url: '' },
    validate: {
      url: (value) => (value.trim().length === 0 ? 'Remote URL is required' : null),
    },
  });

  const handleSubmit = form.onSubmit((values) => {
    addProject.mutate(values.url.trim(), {
      onSuccess: (project) => {
        notifications.show({ message: `Project "${project.name}" added from the remote`, color: 'green' });
        onClose();
        navigate(`/projects/${project.id}`);
      },
      onError: (error) => {
        notifications.show({
          message: error instanceof ApiError ? error.message : 'Failed to add the project from the remote',
          color: 'red',
        });
      },
    });
  });

  return (
    <form onSubmit={handleSubmit}>
      <Stack gap="sm">
        <TextInput
          label="Remote URL"
          description="A git repository of a ReqManTool project (SSH or HTTPS)"
          placeholder="git@example.com:team/project.git"
          required
          data-autofocus
          {...form.getInputProps('url')}
        />
        <Text size="xs" c="dimmed">
          The repository is cloned into the data directory and listed as a project; its remote stays
          configured, so Push and Pull work right away. Access uses your own git credentials (ssh agent /
          credential helper) — ReqManTool never stores them.
        </Text>
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose} disabled={addProject.isPending}>
            Cancel
          </Button>
          <Button type="submit" loading={addProject.isPending}>
            Add project
          </Button>
        </Group>
      </Stack>
    </form>
  );
}
