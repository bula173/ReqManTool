import { Button, Checkbox, Group, Modal, Select, Stack, Text, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useAddAttribute } from '../api/queries';
import { ApiError } from '../api/client';
import type { AttributeType } from '../api/types';
import { REQUIRED_SCOPE_HINT } from '../api/applicability';
import { AttributeModulesChecklist } from './AttributeModulesChecklist';

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
  /** Module ids the attribute applies to; empty = every module. */
  modules: string[];
}

const TYPE_OPTIONS: { value: AttributeType; label: string }[] = [
  { value: 'string', label: 'String' },
  { value: 'text', label: 'Text' },
  { value: 'integer', label: 'Integer' },
  { value: 'boolean', label: 'Boolean' },
  { value: 'enum', label: 'Enum' },
];

function parseChoices(raw: string): string[] {
  return raw
    .split(',')
    .map((c) => c.trim())
    .filter((c) => c.length > 0);
}

function coerceDefault(type: AttributeType, raw: string): unknown {
  if (raw.trim() === '') return null;
  switch (type) {
    case 'integer': {
      const parsed = Number(raw.trim());
      return Number.isNaN(parsed) ? null : parsed;
    }
    case 'boolean':
      return raw.trim() === 'true';
    default:
      return raw;
  }
}

export function AddAttributeModal({ projectId, opened, onClose }: AddAttributeModalProps) {
  const addAttribute = useAddAttribute(projectId);
  const form = useForm<FormValues>({
    initialValues: { id: '', name: '', type: 'string', required: false, default: '', choices: '', modules: [] },
    validate: {
      id: (value) => (value.trim().length === 0 ? 'Attribute id is required' : null),
      name: (value) => (value.trim().length === 0 ? 'Attribute name is required' : null),
      default: (value, values) => {
        const raw = value.trim();
        if (raw === '') return null;
        if (values.type === 'integer' && !/^[+-]?\d+$/.test(raw)) return 'Default must be a whole number';
        if (values.type === 'boolean' && raw !== 'true' && raw !== 'false') return 'Default must be true or false';
        if (values.type === 'enum' && !parseChoices(values.choices).includes(value)) {
          return 'Default must be one of the choices';
        }
        return null;
      },
    },
  });

  const handleSubmit = form.onSubmit((values) => {
    const choices = values.type === 'enum' ? parseChoices(values.choices) : null;
    addAttribute.mutate(
      {
        id: values.id,
        name: values.name,
        type: values.type,
        required: values.required,
        default: coerceDefault(values.type, values.default),
        choices,
        // Only optional attributes can be limited to modules.
        modules: values.required ? [] : values.modules,
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
          <Checkbox
            label="Required"
            {...form.getInputProps('required', { type: 'checkbox' })}
            onChange={(event) => {
              const required = event.currentTarget.checked;
              form.setFieldValue('required', required);
              if (required) form.setFieldValue('modules', []);
            }}
          />
          <TextInput label="Default value" placeholder="Optional" {...form.getInputProps('default')} />
          {form.values.type === 'enum' && (
            <TextInput
              label="Choices"
              placeholder="Comma-separated, e.g. Low, Medium, High"
              {...form.getInputProps('choices')}
            />
          )}
          <AttributeModulesChecklist
            projectId={projectId}
            value={form.values.modules}
            onChange={(modules) => form.setFieldValue('modules', modules)}
            disabled={addAttribute.isPending || form.values.required}
          />
          {form.values.required && (
            <Text size="xs" c="dimmed">
              {REQUIRED_SCOPE_HINT}.
            </Text>
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
