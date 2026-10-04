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
      { attributes: values.attrs, parent_id: parentId },
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
                return <NumberInput key={attr.id} label={label} {...form.getInputProps(path)} />;
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
