import { Button, Group, Modal, Select, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useNavigate } from 'react-router-dom';
import { useCreateProject, useProjects, useTemplates } from '../api/queries';
import { ApiError } from '../api/client';
import { buildProjectForest, projectOptions } from './projectTree';

/** Select values for "no template" / "no parent"; template ids contain ':' and project ids are slugs, so neither can collide. */
const BLANK_TEMPLATE = '__blank__';
const TOP_LEVEL = '__top__';

interface NewProjectModalProps {
  opened: boolean;
  onClose: () => void;
  /** Preselected parent (e.g. "New subproject"); top level if omitted. */
  defaultParentProjectId?: string | null;
  /** Preselected template (File ▸ Load template); blank project if omitted. */
  defaultTemplateId?: string;
}

interface FormValues {
  name: string;
  description: string;
  templateId: string;
  parentProjectId: string;
}

function NewProjectForm({ onClose, defaultParentProjectId, defaultTemplateId }: Omit<NewProjectModalProps, 'opened'>) {
  const navigate = useNavigate();
  const createProject = useCreateProject();
  const templatesQuery = useTemplates();
  const projectsQuery = useProjects();
  const form = useForm<FormValues>({
    initialValues: {
      name: '',
      description: '',
      templateId: defaultTemplateId ?? BLANK_TEMPLATE,
      parentProjectId: defaultParentProjectId ?? TOP_LEVEL,
    },
    validate: {
      name: (value) => (value.trim().length === 0 ? 'Project name is required' : null),
    },
  });

  const templates = templatesQuery.data ?? [];
  const templateOptions = [
    { value: BLANK_TEMPLATE, label: 'Blank project' },
    ...templates.map((t) => ({
      value: t.id,
      label: `${t.name}${t.source === 'user' ? ' (user)' : ''}${t.error ? ' (invalid)' : ''}`,
      disabled: t.error !== null,
    })),
  ];
  const parentOptions = [
    { value: TOP_LEVEL, label: 'None (top level)' },
    ...projectOptions(buildProjectForest(projectsQuery.data ?? [])),
  ];
  const selectedTemplate = templates.find((t) => t.id === form.values.templateId);
  const invalidTemplates = templates.filter((t) => t.error !== null);

  const handleSubmit = form.onSubmit((values) => {
    createProject.mutate(
      {
        name: values.name,
        description: values.description,
        template_id: values.templateId === BLANK_TEMPLATE ? undefined : values.templateId,
        parent_project_id: values.parentProjectId === TOP_LEVEL ? undefined : values.parentProjectId,
      },
      {
        onSuccess: (project) => {
          notifications.show({ message: `Project "${project.name}" created`, color: 'green' });
          form.reset();
          onClose();
          navigate(`/projects/${project.id}`);
        },
        onError: (error) => {
          notifications.show({
            message: error instanceof ApiError ? error.message : 'Failed to create project',
            color: 'red',
          });
        },
      },
    );
  });

  return (
    <form onSubmit={handleSubmit}>
      <Stack gap="sm">
        <TextInput label="Name" placeholder="My Project" required data-autofocus {...form.getInputProps('name')} />
        <Textarea label="Description" placeholder="What is this project about?" {...form.getInputProps('description')} />
        <Select
          label="Template"
          data={templateOptions}
          allowDeselect={false}
          disabled={templatesQuery.isLoading}
          placeholder={templatesQuery.isLoading ? 'Loading templates…' : undefined}
          error={templatesQuery.isError ? 'Failed to load templates; only a blank project is available' : undefined}
          {...form.getInputProps('templateId')}
        />
        {selectedTemplate && (
          <Text size="xs" c="dimmed">
            {selectedTemplate.description || 'No description'}
            {selectedTemplate.modules.length > 0 &&
              ` Modules: ${selectedTemplate.modules.map((m) => m.name).join(', ')}.`}
          </Text>
        )}
        {invalidTemplates.length > 0 && (
          <Text size="xs" c="red">
            {invalidTemplates.length === 1 ? '1 template is' : `${invalidTemplates.length} templates are`} invalid
            and cannot be used:{' '}
            {invalidTemplates.map((t) => `${t.name} (${t.id}): ${t.error}`).join('; ')}
          </Text>
        )}
        <Select
          label="Parent project"
          description="Makes this a subproject (generic → specific) of the chosen project"
          data={parentOptions}
          allowDeselect={false}
          searchable
          disabled={projectsQuery.isLoading}
          error={projectsQuery.isError ? 'Failed to load projects' : undefined}
          {...form.getInputProps('parentProjectId')}
        />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={createProject.isPending}>
            Create
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

export function NewProjectModal({ opened, onClose, defaultParentProjectId, defaultTemplateId }: NewProjectModalProps) {
  // The form lives in a child that unmounts while the modal is closed, so
  // each opening starts fresh with the current default parent.
  return (
    <Modal opened={opened} onClose={onClose} title="New project" centered>
      {opened && (
        <NewProjectForm
          onClose={onClose}
          defaultParentProjectId={defaultParentProjectId}
          defaultTemplateId={defaultTemplateId}
        />
      )}
    </Modal>
  );
}
