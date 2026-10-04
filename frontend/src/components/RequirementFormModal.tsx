import { Button, Checkbox, Group, Modal, NumberInput, Select, Stack, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useEffect } from 'react';
import { useAddRequirement } from '../api/queries';
import { ApiError } from '../api/client';
import type { AttributeDefinition } from '../api/types';

interface RequirementFormModalProps {
  projectId: string;
  moduleId: string;
  attributes: AttributeDefinition[];
  opened: boolean;
  onClose: () => void;
  parentId?: string | null;
}

interface FormValues {
  attrs: Record<string, unknown>;
}

function defaultValueFor(attr: AttributeDefinition): unknown {
  if (attr.default !== null && attr.default !== undefined) return attr.default;
  if (attr.type === 'boolean') return false;
  if (attr.type === 'integer') return '';
  return '';
}

/**
 * Turn form values into the typed attribute values the server accepts:
 * integers as whole numbers, booleans as booleans, enums as one of the
 * choices. An empty integer or enum field is left out (not set).
 */
function toAttributePayload(
  attributes: AttributeDefinition[],
  values: Record<string, unknown>,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const attr of attributes) {
    const value = values[attr.id];
    switch (attr.type) {
      case 'integer': {
        if (value === '' || value === null || value === undefined) break;
        const n = typeof value === 'number' ? value : Number(value);
        if (!Number.isNaN(n)) payload[attr.id] = n;
        break;
      }
      case 'enum':
        if (value === '' || value === null || value === undefined) break;
        payload[attr.id] = value;
        break;
      case 'boolean':
        payload[attr.id] = Boolean(value);
        break;
      default:
        payload[attr.id] = value;
    }
  }
  return payload;
}

function buildInitialValues(attributes: AttributeDefinition[]): FormValues {
  const attrs: Record<string, unknown> = {};
  for (const attr of attributes) {
    attrs[attr.id] = defaultValueFor(attr);
  }
  return { attrs };
}

export function RequirementFormModal({
  projectId,
  moduleId,
  attributes,
  opened,
  onClose,
  parentId = null,
}: RequirementFormModalProps) {
  const editableAttributes = attributes.filter((a) => !a.system);
  const addRequirement = useAddRequirement(projectId, moduleId);
  const form = useForm<FormValues>({
    initialValues: buildInitialValues(editableAttributes),
  });

  // Re-seed the form whenever the modal is opened.
  useEffect(() => {
    if (opened) {
      form.setValues(buildInitialValues(editableAttributes));
      form.resetDirty(buildInitialValues(editableAttributes));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, attributes]);

  const handleSubmit = form.onSubmit((values) => {
    addRequirement.mutate(
      { attributes: toAttributePayload(editableAttributes, values.attrs), parent_id: parentId },
      {
        onSuccess: (requirement) => {
          notifications.show({ message: `Requirement "${requirement.id}" created`, color: 'green' });
          onClose();
        },
        onError: (error) => {
          notifications.show({
            message: error instanceof ApiError ? error.message : 'Failed to create requirement',
            color: 'red',
          });
        },
      },
    );
  });

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={parentId ? `New child of ${parentId}` : 'New requirement'}
      centered
      size="lg"
    >
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          {editableAttributes.map((attr) => {
            const path = `attrs.${attr.id}`;
            const label = attr.required ? `${attr.name} *` : attr.name;
            switch (attr.type) {
              case 'text':
                return <Textarea key={attr.id} label={label} autosize minRows={2} {...form.getInputProps(path)} />;
              case 'integer':
                return (
                  <NumberInput key={attr.id} label={label} allowDecimal={false} {...form.getInputProps(path)} />
                );
              case 'boolean':
                return (
                  <Checkbox
                    key={attr.id}
                    label={label}
                    {...form.getInputProps(path, { type: 'checkbox' })}
                  />
                );
              case 'enum':
                return (
                  <Select
                    key={attr.id}
                    label={label}
                    data={attr.choices ?? []}
                    clearable={!attr.required}
                    {...form.getInputProps(path)}
                  />
                );
              default:
                return <TextInput key={attr.id} label={label} {...form.getInputProps(path)} />;
            }
          })}
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={addRequirement.isPending}>
              Create
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
