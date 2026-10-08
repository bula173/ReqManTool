import { Button, Group, Modal, Select, Stack, Text, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useEffect } from 'react';
import { notifications } from '@mantine/notifications';
import { useModules, useCreateLinkConfiguration } from '../api/queries';
import { ApiError } from '../api/client';
import { LINK_TYPE_PAIRS, linkTypesAllowed, MODULE_TYPE_LABELS, moduleTypeOf } from '../api/moduleTypes';
import type { Module } from '../api/types';
import { ModuleTypeIcon } from './ModuleTypeIcon';

const ALLOWED_PAIRS_HINT = LINK_TYPE_PAIRS.map(
  ([source, target]) => `${MODULE_TYPE_LABELS[source]} → ${MODULE_TYPE_LABELS[target]}`,
).join(', ');

function moduleOption(module: Module, disabled = false) {
  return { value: module.id, label: `${module.name} (${MODULE_TYPE_LABELS[moduleTypeOf(module)]})`, disabled };
}

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

  const modules = modulesQuery.data ?? [];
  const modulesById = new Map(modules.map((m) => [m.id, m]));
  const source = modulesById.get(form.values.source_module_id);
  const target = modulesById.get(form.values.target_module_id);
  const targetInvalid = source !== undefined && target !== undefined && !linkTypesAllowed(moduleTypeOf(source), moduleTypeOf(target));
  // A new source can make the picked target invalid: clear it.
  useEffect(() => {
    if (targetInvalid) form.setFieldValue('target_module_id', '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetInvalid]);
  // Module types decide which modules may be linked (mirrors the server rule).
  const sourceOptions = modules.map((m) =>
    moduleOption(m, !modules.some((t) => linkTypesAllowed(moduleTypeOf(m), moduleTypeOf(t)))),
  );
  const targetOptions = modules.map((m) =>
    moduleOption(m, source !== undefined && !linkTypesAllowed(moduleTypeOf(source), moduleTypeOf(m))),
  );
  const renderOption = ({ option }: { option: { value: string; label: string } }) => (
    <Group gap="xs" wrap="nowrap">
      <ModuleTypeIcon module={modulesById.get(option.value)} size={14} />
      {option.label}
    </Group>
  );

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
            data={sourceOptions}
            renderOption={renderOption}
            required
            disabled={modulesQuery.isLoading}
            error={modulesQuery.isError ? 'Failed to load modules' : undefined}
            {...form.getInputProps('source_module_id')}
          />
          <Select
            label="Target module"
            placeholder={source ? 'Pick a module' : 'Pick the source module first'}
            data={targetOptions}
            renderOption={renderOption}
            required
            disabled={modulesQuery.isLoading || !source}
            {...form.getInputProps('target_module_id')}
          />
          <Text size="xs" c="dimmed">
            Allowed links: {ALLOWED_PAIRS_HINT}.
          </Text>
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
