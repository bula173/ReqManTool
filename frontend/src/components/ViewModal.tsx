import { Button, Checkbox, Group, Modal, Stack, Text, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useCreateView } from '../api/queries';
import { ApiError } from '../api/client';
import type { AttributeDefinition } from '../api/types';

interface ViewModalProps {
  projectId: string;
  attributes: AttributeDefinition[];
  opened: boolean;
  onClose: () => void;
}

interface FormValues {
  name: string;
  attribute_ids: string[];
}

export function ViewModal({ projectId, attributes, opened, onClose }: ViewModalProps) {
  const createView = useCreateView(projectId);
  const form = useForm<FormValues>({
    initialValues: { name: '', attribute_ids: [] },
    validate: {
      name: (value) => (value.trim().length === 0 ? 'Name is required' : null),
      attribute_ids: (value) => (value.length === 0 ? 'Pick at least one attribute' : null),
    },
  });

  const handleSubmit = form.onSubmit((values) => {
    createView.mutate(values, {
      onSuccess: () => {
        notifications.show({ message: `View "${values.name}" created`, color: 'green' });
        form.reset();
        onClose();
      },
      onError: (error) => {
        notifications.show({
          message: error instanceof ApiError ? error.message : 'Failed to create view',
          color: 'red',
        });
      },
    });
  });

  return (
    <Modal opened={opened} onClose={onClose} title="New view" centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          <TextInput label="Name" placeholder="Reviewer summary" required {...form.getInputProps('name')} />
          <div>
            <Text size="sm" fw={500} mb={4}>
              Attributes to show
            </Text>
            <Stack gap={4}>
              {attributes.map((attr) => (
                <Checkbox
                  key={attr.id}
                  label={attr.name}
                  checked={form.values.attribute_ids.includes(attr.id)}
                  onChange={(e) => {
                    const checked = e.currentTarget.checked;
                    const current = form.values.attribute_ids;
                    form.setFieldValue(
                      'attribute_ids',
                      checked ? [...current, attr.id] : current.filter((id) => id !== attr.id),
                    );
                  }}
                />
              ))}
            </Stack>
            {form.errors.attribute_ids && (
              <Text size="xs" c="red" mt={4}>
                {form.errors.attribute_ids}
              </Text>
            )}
          </div>
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={createView.isPending}>
              Create
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
