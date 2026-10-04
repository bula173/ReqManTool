import { Button, Group, Modal, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useSaveProjectAsTemplate } from '../api/queries';
import { ApiError } from '../api/client';
import type { Project } from '../api/types';

interface SaveAsTemplateModalProps {
  project: Project;
  opened: boolean;
  onClose: () => void;
}

interface FormValues {
  name: string;
  description: string;
}

function SaveAsTemplateForm({ project, onClose }: Omit<SaveAsTemplateModalProps, 'opened'>) {
  const saveAsTemplate = useSaveProjectAsTemplate(project.id);
  const form = useForm<FormValues>({
    initialValues: { name: project.name, description: project.description },
    validate: {
      name: (value) => (value.trim().length === 0 ? 'Template name is required' : null),
    },
  });

  const handleSubmit = form.onSubmit((values) => {
    saveAsTemplate.mutate(
      { name: values.name.trim(), description: values.description },
      {
        onSuccess: (template) => {
          notifications.show({ message: `Template "${template.name}" saved`, color: 'green' });
          onClose();
        },
        onError: (error) => {
          if (error instanceof ApiError && error.status === 409) {
            form.setFieldError('name', error.message);
            return;
          }
          notifications.show({
            message: error instanceof ApiError ? error.message : 'Failed to save template',
            color: 'red',
          });
        },
      },
    );
  });

  return (
    <form onSubmit={handleSubmit}>
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Saves this project's attributes, modules, link configurations and views as a reusable
          template. Requirements are not included.
        </Text>
        <TextInput label="Template name" required data-autofocus {...form.getInputProps('name')} />
        <Textarea label="Description" autosize minRows={2} maxRows={6} {...form.getInputProps('description')} />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={saveAsTemplate.isPending}>
            Save template
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

export function SaveAsTemplateModal({ project, opened, onClose }: SaveAsTemplateModalProps) {
  return (
    <Modal opened={opened} onClose={onClose} title="Save as template" centered>
      {opened && <SaveAsTemplateForm project={project} onClose={onClose} />}
    </Modal>
  );
}
