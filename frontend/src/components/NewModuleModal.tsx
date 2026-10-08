import { Button, Group, Modal, Select, Stack, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useNavigate } from 'react-router-dom';
import { useCreateModule, useModuleTree } from '../api/queries';
import { ApiError } from '../api/client';
import { MODULE_TYPE_LABELS, MODULE_TYPES } from '../api/moduleTypes';
import type { ModuleType } from '../api/types';
import { ModuleTypeIcon } from './ModuleTypeIcon';
import { folderPickerOptions, TOP_LEVEL_FOLDER } from './moduleTree';

const TYPE_OPTIONS = MODULE_TYPES.map((type) => ({ value: type, label: MODULE_TYPE_LABELS[type] }));

interface NewModuleModalProps {
  projectId: string | null;
  /** The module folder preselected for the new module (null: the top level). */
  folderId?: string | null;
  onClose: () => void;
}

interface FormValues {
  name: string;
  prefix: string;
  description: string;
  type: ModuleType;
  /** `TOP_LEVEL_FOLDER` or a module folder id. */
  folder: string;
}

export function NewModuleModal({ projectId, folderId = null, onClose }: NewModuleModalProps) {
  const navigate = useNavigate();
  const createModule = useCreateModule(projectId ?? '');
  const treeQuery = useModuleTree(projectId ?? undefined);
  const folderOptions = folderPickerOptions(treeQuery.data);
  const form = useForm<FormValues>({
    initialValues: { name: '', prefix: '', description: '', type: 'requirements', folder: folderId ?? TOP_LEVEL_FOLDER },
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
    const { folder, ...body } = values;
    createModule.mutate({ ...body, folder_id: folder === TOP_LEVEL_FOLDER ? null : folder }, {
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
          {/* Shown once the project has folders (or one was preselected while the tree loads). */}
          {(folderOptions.length > 1 || folderId !== null) && (
            <Select
              label="Folder"
              description="Where the module is listed; folders only organise the module list"
              data={folderOptions}
              allowDeselect={false}
              disabled={treeQuery.isLoading}
              error={treeQuery.isError ? 'Failed to load the module folders' : undefined}
              {...form.getInputProps('folder')}
            />
          )}
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
