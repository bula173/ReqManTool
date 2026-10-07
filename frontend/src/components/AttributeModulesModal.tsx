import { Button, Checkbox, Group, Modal, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useSetAttributeModules } from '../api/queries';
import type { AttributeDefinition } from '../api/types';
import { AttributeModulesChecklist } from './AttributeModulesChecklist';

interface AttributeModulesModalProps {
  projectId: string;
  /** The attribute being edited; the modal is open while it is set. */
  attribute: AttributeDefinition | null;
  onClose: () => void;
}

/** Edits which modules an existing attribute applies to. */
export function AttributeModulesModal({ projectId, attribute, onClose }: AttributeModulesModalProps) {
  return (
    <Modal opened={attribute !== null} onClose={onClose} title={`Modules for "${attribute?.name ?? ''}"`} centered>
      {attribute && <AttributeModulesForm key={attribute.id} projectId={projectId} attribute={attribute} onClose={onClose} />}
    </Modal>
  );
}

function AttributeModulesForm({
  projectId,
  attribute,
  onClose,
}: {
  projectId: string;
  attribute: AttributeDefinition;
  onClose: () => void;
}) {
  const setModules = useSetAttributeModules(projectId);
  const [modules, setModulesValue] = useState<string[]>(attribute.modules ?? []);
  const [dropValues, setDropValues] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = () => {
    setError(null);
    setModules.mutate(
      { attrId: attribute.id, modules, dropValues },
      {
        onSuccess: (result) => {
          const dropped = result.dropped_values.length;
          notifications.show({
            message:
              dropped > 0
                ? `Modules of "${attribute.name}" updated; ${dropped} value(s) removed`
                : `Modules of "${attribute.name}" updated`,
            color: 'green',
          });
          onClose();
        },
        onError: (err) => setError(err instanceof ApiError ? err.message : 'Failed to update modules'),
      },
    );
  };

  return (
    <Stack gap="sm">
      <AttributeModulesChecklist
        projectId={projectId}
        value={modules}
        onChange={setModulesValue}
        disabled={setModules.isPending}
      />
      <Checkbox
        label="Remove existing values in modules left out"
        description="Without this, saving is refused while such requirements still have a value."
        checked={dropValues}
        onChange={(event) => setDropValues(event.currentTarget.checked)}
        disabled={setModules.isPending}
      />
      {error && (
        <Text size="sm" c="red">
          {error}
        </Text>
      )}
      <Group justify="flex-end" mt="sm">
        <Button variant="default" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={handleSave} loading={setModules.isPending}>
          Save
        </Button>
      </Group>
    </Stack>
  );
}
