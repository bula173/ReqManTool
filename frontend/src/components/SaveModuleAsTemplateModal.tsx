import { Button, Group, Modal, Select, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useSaveModuleAsTemplate, useViews } from '../api/queries';
import { ApiError } from '../api/client';

interface SaveModuleAsTemplateModalProps {
  projectId: string;
  moduleId: string;
  moduleName: string;
  onClose: () => void;
}

/** The view picker's "no view" entry. */
const NO_VIEW = '';

interface FormValues {
  name: string;
  description: string;
  /** A view id of the project, or `NO_VIEW`. */
  view: string;
}

/**
 * "Save module as template": writes the module's name, prefix, type, the
 * attributes that apply to it, an optional view and its headings (as a heading
 * skeleton) as a user module template. Requirements other than headings and
 * link configurations are not included; the server owns every rule.
 */
export function SaveModuleAsTemplateModal({ projectId, moduleId, moduleName, onClose }: SaveModuleAsTemplateModalProps) {
  const save = useSaveModuleAsTemplate(projectId, moduleId);
  const viewsQuery = useViews(projectId);
  const form = useForm<FormValues>({
    initialValues: { name: moduleName, description: '', view: NO_VIEW },
    validate: {
      name: (value) => (value.trim().length === 0 ? 'Template name is required' : null),
    },
  });

  const handleSubmit = form.onSubmit((values) => {
    save.mutate(
      {
        name: values.name.trim(),
        description: values.description,
        view_id: values.view === NO_VIEW ? null : values.view,
      },
      {
        onSuccess: (template) => {
          notifications.show({
            message: `Module template "${template.name}" saved (${template.headings} heading${template.headings === 1 ? '' : 's'})`,
            color: 'green',
          });
          onClose();
        },
        onError: (error) => {
          notifications.show({
            message: error instanceof ApiError ? error.message : 'Failed to save module template',
            color: 'red',
          });
        },
      },
    );
  });

  const viewOptions = [
    { value: NO_VIEW, label: 'None' },
    ...(viewsQuery.data ?? []).map((view) => ({ value: view.id, label: view.name })),
  ];

  return (
    <Modal opened onClose={onClose} title="Save module as template" centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            Saves the module&apos;s name, prefix and type, the attributes that apply to it and its headings
            (Object Type = Heading) as a module template. Other requirements and links are not included.
          </Text>
          <TextInput label="Template name" required data-autofocus {...form.getInputProps('name')} />
          <Textarea label="Description" {...form.getInputProps('description')} />
          <Select
            label="View"
            description="A view added with the module, unless the project already has a view of that name"
            data={viewOptions}
            allowDeselect={false}
            disabled={viewsQuery.isLoading}
            error={viewsQuery.isError ? 'Failed to load the views' : undefined}
            {...form.getInputProps('view')}
          />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={save.isPending}>
              Save template
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
