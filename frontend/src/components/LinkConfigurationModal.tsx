import { Button, Group, Modal, Select, Stack, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useModules, useCreateLinkConfiguration } from '../api/queries';
import { ApiError } from '../api/client';

interface LinkConfigurationModalProps {
  projectId: string;
  opened: boolean;
  onClose: () => void;
}

interface FormValues {
  name: string;
  source_module_id: string;
  target_module_id: string;
}

export function LinkConfigurationModal({ projectId, opened, onClose }: LinkConfigurationModalProps) {
  const modulesQuery = useModules(projectId);
  const createLinkConfiguration = useCreateLinkConfiguration(projectId);
  const form = useForm<FormValues>({
    initialValues: { name: '', source_module_id: '', target_module_id: '' },
    validate: {
      name: (value) => (value.trim().length === 0 ? 'Name is required' : null),
      source_module_id: (value) => (value.length === 0 ? 'Source module is required' : null),
      target_module_id: (value) => (value.length === 0 ? 'Target module is required' : null),
    },
  });

  const moduleOptions = (modulesQuery.data ?? []).map((m) => ({ value: m.id, label: m.name }));

  const handleSubmit = form.onSubmit((values) => {
    createLinkConfiguration.mutate(values, {
      onSuccess: () => {
        notifications.show({ message: `Link configuration "${values.name}" created`, color: 'green' });
        form.reset();
        onClose();
      },
      onError: (error) => {
        notifications.show({
          message: error instanceof ApiError ? error.message : 'Failed to create link configuration',
          color: 'red',
        });
      },
    });
  });

  return (
    <Modal opened={opened} onClose={onClose} title="New link configuration" centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          <TextInput label="Name" placeholder="Verifies" required {...form.getInputProps('name')} />
          <Select
            label="Source module"
            placeholder="Pick a module"
            data={moduleOptions}
            required
            {...form.getInputProps('source_module_id')}
          />
          <Select
            label="Target module"
            placeholder="Pick a module"
            data={moduleOptions}
            required
            {...form.getInputProps('target_module_id')}
          />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={createLinkConfiguration.isPending}>
              Create
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
