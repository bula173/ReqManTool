import { Button, Group, Modal, Stack, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useNavigate } from 'react-router-dom';
import { useCreateProject } from '../api/queries';
import { ApiError } from '../api/client';

interface NewProjectModalProps {
  opened: boolean;
  onClose: () => void;
}

interface FormValues {
  name: string;
  description: string;
}

export function NewProjectModal({ opened, onClose }: NewProjectModalProps) {
  const navigate = useNavigate();
  const createProject = useCreateProject();
  const form = useForm<FormValues>({
    initialValues: { name: '', description: '' },
    validate: {
      name: (value) => (value.trim().length === 0 ? 'Project name is required' : null),
    },
  });

  const handleSubmit = form.onSubmit((values) => {
    createProject.mutate(values, {
      onSuccess: (project) => {
        notifications.show({ message: `Project "${project.name}" created`, color: 'green' });
        form.reset();
        onClose();
        navigate(`/projects/${project.id}`);
      },
      onError: (error) => {
        notifications.show({
          message: error instanceof ApiError ? error.message : 'Failed to create project',
          color: 'red',
        });
      },
    });
  });

  return (
    <Modal opened={opened} onClose={onClose} title="New project" centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          <TextInput label="Name" placeholder="My Project" required {...form.getInputProps('name')} />
          <Textarea label="Description" placeholder="What is this project about?" {...form.getInputProps('description')} />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={createProject.isPending}>
              Create
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
