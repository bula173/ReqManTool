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

/**
 * Whether the attribute has a default the server applies to new
 * requirements. Mirrors the server rule: a stored default that is not valid
 * for the attribute's type (legacy data) is ignored. A blank string default
 * is not usable either: it cannot fill a required attribute.
 */
function hasUsableDefault(attr: AttributeDefinition): boolean {
  const value = attr.default;
  if (value === null || value === undefined) return false;
  switch (attr.type) {
    case 'integer':
      return typeof value === 'number' && Number.isInteger(value);
    case 'boolean':
      return typeof value === 'boolean';
    case 'enum':
      return typeof value === 'string' && (attr.choices ?? []).includes(value);
    default:
      return typeof value === 'string' && value.trim() !== '';
  }
}

function defaultValueFor(attr: AttributeDefinition): unknown {
  if (hasUsableDefault(attr)) return attr.default;
  if (attr.type === 'boolean') return false;
  return '';
}

function isBlank(value: unknown): boolean {
  return value === null || value === undefined || (typeof value === 'string' && value.trim() === '');
}

/**
 * Turn form values into the typed attribute values the server accepts:
 * integers as whole numbers, booleans as booleans, enums as one of the
 * choices. An empty integer or enum field is sent as null when the
 * attribute has a default (the user cleared it, so the server must not
 * apply the default), and left out otherwise.
 */
function toAttributePayload(
  attributes: AttributeDefinition[],
  values: Record<string, unknown>,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const attr of attributes) {
    const value = values[attr.id];
    const cleared = value === '' || value === null || value === undefined;
    switch (attr.type) {
      case 'integer': {
        if (cleared) {
          if (hasUsableDefault(attr)) payload[attr.id] = null;
          break;
        }
        const n = typeof value === 'number' ? value : Number(value);
        if (!Number.isNaN(n)) payload[attr.id] = n;
        break;
      }
      case 'enum':
        if (cleared) {
          if (hasUsableDefault(attr)) payload[attr.id] = null;
          break;
        }
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
    // The server rejects a blank required attribute; block submitting it here.
    validate: (values) => {
      const errors: Record<string, string> = {};
      for (const attr of editableAttributes) {
        if (attr.required && attr.type !== 'boolean' && isBlank(values.attrs[attr.id])) {
          errors[`attrs.${attr.id}`] = `${attr.name} is required`;
        }
      }
      return errors;
    },
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
