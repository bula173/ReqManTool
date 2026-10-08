import { Button, Checkbox, Group, Input, Modal, NumberInput, Select, Stack, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useEffect, useState } from 'react';
import { useAddRequirement, useMoveRequirement } from '../api/queries';
import { ApiError } from '../api/client';
import type { AttributeDefinition } from '../api/types';
import { LazyRichTextField } from './LazyRichTextField';
import { richTextToPlain } from './richText';

interface RequirementFormModalProps {
  projectId: string;
  moduleId: string;
  attributes: AttributeDefinition[];
  opened: boolean;
  onClose: () => void;
  parentId?: string | null;
  /**
   * Places the new requirement at this 0-based position among its siblings
   * (Add sibling: right after the clicked one) with the server's move after
   * creating it. Omitted: the server appends it as the last child.
   */
  insertIndex?: number;
  /** The sibling the new requirement is placed after (dialog title only). */
  afterId?: string;
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
      return typeof value === 'string' && richTextToPlain(value).trim() !== '';
  }
}

function defaultValueFor(attr: AttributeDefinition): unknown {
  if (hasUsableDefault(attr)) return attr.default;
  if (attr.type === 'boolean') return false;
  return '';
}

/** Blank as the server judges it: rich text by its plain projection. */
function isBlank(value: unknown): boolean {
  return value === null || value === undefined || (typeof value === 'string' && richTextToPlain(value).trim() === '');
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
  insertIndex,
  afterId,
}: RequirementFormModalProps) {
  const editableAttributes = attributes.filter((a) => !a.system);
  const addRequirement = useAddRequirement(projectId, moduleId);
  const moveRequirement = useMoveRequirement(projectId, moduleId);
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

  // Bumped on every re-seed: the rich text editors read their value once, so they are re-created.
  const [seed, setSeed] = useState(0);

  // Re-seed the form whenever the modal is opened.
  useEffect(() => {
    if (opened) {
      form.setValues(buildInitialValues(editableAttributes));
      form.resetDirty(buildInitialValues(editableAttributes));
      setSeed((n) => n + 1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, attributes]);

  const handleSubmit = form.onSubmit((values) => {
    addRequirement.mutate(
      { attributes: toAttributePayload(editableAttributes, values.attrs), parent_id: parentId },
      {
        onSuccess: (requirement) => {
          notifications.show({ message: `Requirement "${requirement.id}" created`, color: 'green' });
          if (insertIndex === undefined) {
            onClose();
            return;
          }
          // Then placed among its siblings (the dialog stays open, busy, meanwhile).
          moveRequirement
            .mutateAsync({ id: requirement.id, parentId, index: insertIndex })
            .catch((error) =>
              notifications.show({
                message:
                  error instanceof ApiError
                    ? `"${requirement.id}" was added as the last sibling: ${error.message}`
                    : `"${requirement.id}" was added as the last sibling`,
                color: 'yellow',
              }),
            )
            .finally(onClose);
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
      title={afterId ? `New requirement after ${afterId}` : parentId ? `New child of ${parentId}` : 'New requirement'}
      centered
      size="lg"
    >
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          {editableAttributes.map((attr) => {
            const path = `attrs.${attr.id}`;
            const label = attr.required ? `${attr.name} *` : attr.name;
            switch (attr.type) {
              case 'text': {
                const input = form.getInputProps(path);
                return (
                  <Input.Wrapper key={`${attr.id}-${seed}`} label={label} error={input.error}>
                    <LazyRichTextField
                      value={input.value}
                      onChange={(next) => form.setFieldValue(path, next)}
                      aria-label={attr.name}
                    />
                  </Input.Wrapper>
                );
              }
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
            <Button type="submit" loading={addRequirement.isPending || moveRequirement.isPending}>
              Create
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
