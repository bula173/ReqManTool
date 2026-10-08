import { Button, Group, Modal, Select, Stack, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useNavigate } from 'react-router-dom';
import { useCreateModule } from '../api/queries';
import { ApiError } from '../api/client';
import { MODULE_TYPE_LABELS, MODULE_TYPES } from '../api/moduleTypes';
import type { ModuleType } from '../api/types';
import { ModuleTypeIcon } from './ModuleTypeIcon';

const TYPE_OPTIONS = MODULE_TYPES.map((type) => ({ value: type, label: MODULE_TYPE_LABELS[type] }));

interface NewModuleModalProps {
  projectId: string | null;
  onClose: () => void;
}

interface FormValues {
  name: string;
  prefix: string;
  description: string;
  type: ModuleType;
}

export function NewModuleModal({ projectId, onClose }: NewModuleModalProps) {
  const navigate = useNavigate();
  const createModule = useCreateModule(projectId ?? '');
  const form = useForm<FormValues>({
    initialValues: { name: '', prefix: '', description: '', type: 'requirements' },
    validate: {
      name: (value) => (value.trim().length === 0 ? 'Module name is required' : null),
      prefix: (value) =>
        /^[A-Za-z][A-Za-z0-9_-]*$/.test(value.trim())
          ? null
          : 'Prefix must start with a letter (letters, digits, _, - only)',
    },
  });

  const handleSubmit = form.onSubmit((values) => {
    if (!projectId) return;
    createModule.mutate(values, {
      onSuccess: (module) => {
        notifications.show({ message: `Module "${module.name}" created`, color: 'green' });
        form.reset();
        onClose();
        navigate(`/projects/${projectId}/modules/${module.id}`);
      },
      onError: (error) => {
        notifications.show({
          message: error instanceof ApiError ? error.message : 'Failed to create module',
          color: 'red',
        });
      },
    });
  });

  return (
    <Modal opened={projectId !== null} onClose={onClose} title="New module" centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          <TextInput label="Name" placeholder="Core Requirements" required {...form.getInputProps('name')} />
          <TextInput label="Prefix" placeholder="REQ" required {...form.getInputProps('prefix')} />
          <Select
            label="Type"
            description="What the module holds; it decides which links and attributes fit it and cannot be changed later"
            data={TYPE_OPTIONS}
            allowDeselect={false}
            leftSection={<ModuleTypeIcon type={form.values.type} size={16} />}
            renderOption={({ option }) => (
              <Group gap="xs" wrap="nowrap">
                <ModuleTypeIcon type={option.value as ModuleType} size={16} />
                {option.label}
              </Group>
            )}
            {...form.getInputProps('type')}
          />
          <Textarea label="Description" {...form.getInputProps('description')} />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={createModule.isPending}>
              Create
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
