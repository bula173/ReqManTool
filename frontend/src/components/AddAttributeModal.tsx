import { Button, Checkbox, Group, Modal, Select, Stack, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useAddAttribute } from '../api/queries';
import { ApiError } from '../api/client';
import type { AttributeType } from '../api/types';

interface AddAttributeModalProps {
  projectId: string;
  opened: boolean;
  onClose: () => void;
}

interface FormValues {
  id: string;
  name: string;
  type: AttributeType;
  required: boolean;
  default: string;
  choices: string;
}

const TYPE_OPTIONS: { value: AttributeType; label: string }[] = [
  { value: 'string', label: 'String' },
  { value: 'text', label: 'Text' },
  { value: 'integer', label: 'Integer' },
  { value: 'boolean', label: 'Boolean' },
  { value: 'enum', label: 'Enum' },
];

function coerceDefault(type: AttributeType, raw: string): unknown {
  if (raw.trim() === '') return null;
  switch (type) {
    case 'integer': {
      const parsed = Number(raw);
      return Number.isNaN(parsed) ? null : parsed;
    }
    case 'boolean':
      return raw === 'true';
    default:
      return raw;
  }
}

export function AddAttributeModal({ projectId, opened, onClose }: AddAttributeModalProps) {
  const addAttribute = useAddAttribute(projectId);
  const form = useForm<FormValues>({
    initialValues: { id: '', name: '', type: 'string', required: false, default: '', choices: '' },
    validate: {
      id: (value) => (value.trim().length === 0 ? 'Attribute id is required' : null),
      name: (value) => (value.trim().length === 0 ? 'Attribute name is required' : null),
    },
  });

  const handleSubmit = form.onSubmit((values) => {
    const choices =
      values.type === 'enum'
        ? values.choices
            .split(',')
            .map((c) => c.trim())
            .filter((c) => c.length > 0)
        : null;
    addAttribute.mutate(
      {
        id: values.id,
        name: values.name,
        type: values.type,
        required: values.required,
        default: coerceDefault(values.type, values.default),
        choices,
      },
      {
        onSuccess: () => {
          notifications.show({ message: `Attribute "${values.name}" added`, color: 'green' });
          form.reset();
          onClose();
        },
        onError: (error) => {
          notifications.show({
            message: error instanceof ApiError ? error.message : 'Failed to add attribute',
            color: 'red',
          });
        },
      },
    );
  });

  return (
    <Modal opened={opened} onClose={onClose} title="Add attribute" centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          <TextInput label="Attribute id" placeholder="priority" required {...form.getInputProps('id')} />
          <TextInput label="Name" placeholder="Priority" required {...form.getInputProps('name')} />
          <Select
            label="Type"
            data={TYPE_OPTIONS}
            allowDeselect={false}
            {...form.getInputProps('type')}
          />
          <Checkbox label="Required" {...form.getInputProps('required', { type: 'checkbox' })} />
          <TextInput label="Default value" placeholder="Optional" {...form.getInputProps('default')} />
          {form.values.type === 'enum' && (
            <TextInput
              label="Choices"
              placeholder="Comma-separated, e.g. Low, Medium, High"
              {...form.getInputProps('choices')}
            />
          )}
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={addAttribute.isPending}>
              Add
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
