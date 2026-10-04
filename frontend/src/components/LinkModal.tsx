import { Button, Group, Modal, Select, Stack, Text } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useEffect } from 'react';
import { useLinkConfigurations, useLinkRequirements, useRequirements } from '../api/queries';
import { ApiError } from '../api/client';
import type { Requirement } from '../api/types';

interface LinkModalProps {
  projectId: string;
  moduleId: string;
  source: Requirement | null;
  onClose: () => void;
}

interface FormValues {
  config_id: string;
  target_id: string;
}

export function LinkModal({ projectId, moduleId, source, onClose }: LinkModalProps) {
  const linkConfigurationsQuery = useLinkConfigurations(projectId);
  const linkRequirements = useLinkRequirements(projectId);
  const form = useForm<FormValues>({
    initialValues: { config_id: '', target_id: '' },
    validate: {
      config_id: (value) => (value.trim().length === 0 ? 'Link configuration is required' : null),
      target_id: (value) => (value.trim().length === 0 ? 'Target requirement is required' : null),
    },
  });

  useEffect(() => {
    if (source) form.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  const availableConfigs = (linkConfigurationsQuery.data ?? []).filter(
    (c) => c.source_module_id === moduleId,
  );
  const selectedConfig = availableConfigs.find((c) => c.id === form.values.config_id) ?? null;

  const targetRequirementsQuery = useRequirements(projectId, selectedConfig?.target_module_id);
  const targetOptions = (targetRequirementsQuery.data ?? [])
    .filter((r) => r.id !== source?.id)
    .map((r) => ({ value: r.id, label: r.id }));

  const handleSubmit = form.onSubmit((values) => {
    if (!source) return;
    linkRequirements.mutate(
      { config_id: values.config_id, source_id: source.id, target_id: values.target_id },
      {
        onSuccess: () => {
          notifications.show({ message: `Linked ${source.id} -> ${values.target_id}`, color: 'green' });
          onClose();
        },
        onError: (error) => {
          notifications.show({
            message: error instanceof ApiError ? error.message : 'Failed to link requirements',
            color: 'red',
          });
        },
      },
    );
  });

  return (
    <Modal opened={source !== null} onClose={onClose} title={`Link ${source?.id ?? ''}`} centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          {availableConfigs.length === 0 && (
            <Text size="sm" c="dimmed">
              No link configurations start from this module. Create one on the project page first.
            </Text>
          )}
          <Select
            label="Link configuration"
            placeholder="Pick a link configuration"
            data={availableConfigs.map((c) => ({ value: c.id, label: c.name }))}
            required
            disabled={availableConfigs.length === 0}
            {...form.getInputProps('config_id')}
            onChange={(value) => {
              form.setFieldValue('config_id', value ?? '');
              form.setFieldValue('target_id', '');
            }}
          />
          <Select
            label="Target requirement"
            placeholder={selectedConfig ? 'Pick a requirement' : 'Pick a link configuration first'}
            searchable
            data={targetOptions}
            required
            disabled={!selectedConfig}
            {...form.getInputProps('target_id')}
          />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={linkRequirements.isPending} disabled={availableConfigs.length === 0}>
              Link
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
