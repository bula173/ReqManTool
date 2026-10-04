import { Button, Group, Modal, Select, Stack } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useEffect } from 'react';
import { useMoveRequirement } from '../api/queries';
import { ApiError } from '../api/client';
import type { Requirement } from '../api/types';

interface MoveRequirementModalProps {
  projectId: string;
  moduleId: string;
  source: Requirement | null;
  allRequirements: Requirement[];
  onClose: () => void;
}

interface FormValues {
  parent_id: string;
}

const ROOT_VALUE = '__root__';

export function MoveRequirementModal({
  projectId,
  moduleId,
  source,
  allRequirements,
  onClose,
}: MoveRequirementModalProps) {
  const moveRequirement = useMoveRequirement(projectId, moduleId);
  const form = useForm<FormValues>({
    initialValues: { parent_id: ROOT_VALUE },
  });

  useEffect(() => {
    if (source) form.setValues({ parent_id: source.parent_id ?? ROOT_VALUE });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  const parentOptions = [
    { value: ROOT_VALUE, label: 'Root (no parent)' },
    ...allRequirements.filter((r) => r.id !== source?.id).map((r) => ({ value: r.id, label: r.id })),
  ];

  const handleSubmit = form.onSubmit((values) => {
    if (!source) return;
    const parentId = values.parent_id === ROOT_VALUE ? null : values.parent_id;
    moveRequirement.mutate(
      { id: source.id, parentId },
      {
        onSuccess: () => {
          notifications.show({ message: `Moved ${source.id}`, color: 'green' });
          onClose();
        },
        onError: (error) => {
          notifications.show({
            message: error instanceof ApiError ? error.message : 'Failed to move requirement',
            color: 'red',
          });
        },
      },
    );
  });

  return (
    <Modal opened={source !== null} onClose={onClose} title={`Move ${source?.id ?? ''}`} centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          <Select
            label="New parent"
            placeholder="Pick a parent requirement"
            searchable
            data={parentOptions}
            {...form.getInputProps('parent_id')}
          />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={moveRequirement.isPending}>
              Move
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
