import { useState } from 'react';
import { Button, Group, Modal, Select, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useNavigate } from 'react-router-dom';
import { useCreateModule, useCreateModuleFromTemplate, useModuleTemplates, useModuleTree } from '../api/queries';
import { ApiError } from '../api/client';
import { MODULE_TYPE_LABELS, MODULE_TYPES } from '../api/moduleTypes';
import type { ModuleTemplateSummary, ModuleType } from '../api/types';
import { ModuleTypeIcon } from './ModuleTypeIcon';
import { folderPickerOptions, TOP_LEVEL_FOLDER } from './moduleTree';

const TYPE_OPTIONS = MODULE_TYPES.map((type) => ({ value: type, label: MODULE_TYPE_LABELS[type] }));

interface NewModuleModalProps {
  projectId: string | null;
  /** The module folder preselected for the new module (null: the top level). */
  folderId?: string | null;
  /** Open with the module template picker, its list fetched right away. */
  fromTemplate?: boolean;
  onClose: () => void;
}

/** The template picker's "no template" entry. */
const NO_TEMPLATE = '';

function templateLabel(template: ModuleTemplateSummary): string {
  return `${template.name}${template.source === 'user' ? ' (user)' : ''}${template.error ? ' (invalid)' : ''}`;
}

function templateDescription(template: ModuleTemplateSummary): string {
  if (template.error) return template.error;
  const parts = [
    template.headings > 0 ? `${template.headings} heading${template.headings === 1 ? '' : 's'}` : 'no headings',
  ];
  if (template.attributes.length > 0) parts.push(`attributes: ${template.attributes.join(', ')}`);
  if (template.view) parts.push(`view "${template.view}"`);
  return `${template.description ? `${template.description} ` : ''}(${parts.join('; ')})`;
}

interface FormValues {
  name: string;
  prefix: string;
  description: string;
  type: ModuleType;
  /** `TOP_LEVEL_FOLDER` or a module folder id. */
  folder: string;
  /** A module template id, or `NO_TEMPLATE`. */
  template: string;
}

export function NewModuleModal({ projectId, folderId = null, fromTemplate = false, onClose }: NewModuleModalProps) {
  const navigate = useNavigate();
  const createModule = useCreateModule(projectId ?? '');
  const createFromTemplate = useCreateModuleFromTemplate(projectId ?? '');
  const treeQuery = useModuleTree(projectId ?? undefined);
  const folderOptions = folderPickerOptions(treeQuery.data);
  // The module template list is fetched lazily: when the picker opens (or right away from "New module from template").
  const [templatesRequested, setTemplatesRequested] = useState(fromTemplate);
  const templatesQuery = useModuleTemplates(templatesRequested && projectId !== null);
  const templates = templatesQuery.data ?? [];
  const form = useForm<FormValues>({
    initialValues: {
      name: '',
      prefix: '',
      description: '',
      type: 'requirements',
      folder: folderId ?? TOP_LEVEL_FOLDER,
      template: NO_TEMPLATE,
    },
    validate: {
      name: (value) => (value.trim().length === 0 ? 'Module name is required' : null),
      prefix: (value) =>
        /^[A-Za-z][A-Za-z0-9_-]*$/.test(value.trim())
          ? null
          : 'Prefix must start with a letter (letters, digits, _, - only)',
    },
  });

  const selectedTemplate = templates.find((t) => t.id === form.values.template) ?? null;

  const chooseTemplate = (templateId: string | null) => {
    const template = templates.find((t) => t.id === templateId) ?? null;
    form.setFieldValue('template', template ? template.id : NO_TEMPLATE);
    // The template's module defaults; they can still be edited (the type cannot).
    if (template?.module) {
      form.setValues({
        name: template.module.name,
        prefix: template.module.prefix,
        description: template.module.description,
        type: template.module.type,
      });
    }
  };

  const notifyError = (error: unknown) =>
    notifications.show({
      message: error instanceof ApiError ? error.message : 'Failed to create module',
      color: 'red',
    });

  const handleSubmit = form.onSubmit((values) => {
    if (!projectId) return;
    const { folder, template, ...body } = values;
    const folder_id = folder === TOP_LEVEL_FOLDER ? null : folder;
    if (template !== NO_TEMPLATE) {
      createFromTemplate.mutate(
        {
          template_id: template,
          name: body.name.trim(),
          prefix: body.prefix.trim(),
          description: body.description,
          folder_id,
        },
        {
          onSuccess: (result) => {
            const added = result.added_attributes.length;
            notifications.show({
              message:
                `Module "${result.module.name}" created with ${result.headings} skeleton object` +
                `${result.headings === 1 ? '' : 's'}` +
                (added > 0 ? `; ${added} attribute${added === 1 ? '' : 's'} added` : ''),
              color: 'green',
            });
            form.reset();
            onClose();
            navigate(`/projects/${projectId}/modules/${result.module.id}`);
          },
          onError: notifyError,
        },
      );
      return;
    }
    createModule.mutate({ ...body, folder_id }, {
      onSuccess: (module) => {
        notifications.show({ message: `Module "${module.name}" created`, color: 'green' });
        form.reset();
        onClose();
        navigate(`/projects/${projectId}/modules/${module.id}`);
      },
      onError: notifyError,
    });
  });

  const templateOptions = [
    { value: NO_TEMPLATE, label: 'None (empty module)' },
    ...templates.map((t) => ({ value: t.id, label: templateLabel(t), disabled: t.error !== null })),
  ];
  let templateHint: string | undefined;
  if (templatesQuery.isLoading && templatesRequested) templateHint = 'Loading module templates…';
  else if (templatesQuery.isSuccess && templates.length === 0) templateHint = 'No module templates';
  else if (selectedTemplate) templateHint = templateDescription(selectedTemplate);

  return (
    <Modal
      opened={projectId !== null}
      onClose={onClose}
      title={fromTemplate ? 'New module from template' : 'New module'}
      centered
    >
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          <Select
            label="Template"
            description="A module template adds its heading skeleton and the attributes the module needs"
            data={templateOptions}
            value={form.values.template}
            onChange={chooseTemplate}
            onDropdownOpen={() => setTemplatesRequested(true)}
            allowDeselect={false}
            data-autofocus={fromTemplate || undefined}
            error={templatesQuery.isError ? 'Failed to load module templates' : undefined}
          />
          {templateHint && (
            <Text size="xs" c="dimmed">
              {templateHint}
            </Text>
          )}
          <TextInput label="Name" placeholder="Core Requirements" required {...form.getInputProps('name')} />
          <TextInput label="Prefix" placeholder="REQ" required {...form.getInputProps('prefix')} />
          <Select
            label="Type"
            description="What the module holds; it decides which links and attributes fit it and cannot be changed later"
            data={TYPE_OPTIONS}
            allowDeselect={false}
            leftSection={<ModuleTypeIcon type={form.values.type} size={16} />}
            disabled={selectedTemplate !== null}
            renderOption={({ option }) => (
              <Group gap="xs" wrap="nowrap">
                <ModuleTypeIcon type={option.value as ModuleType} size={16} />
                {option.label}
              </Group>
            )}
            {...form.getInputProps('type')}
          />
          {/* Shown once the project has folders (or one was preselected while the tree loads). */}
          {(folderOptions.length > 1 || folderId !== null) && (
            <Select
              label="Folder"
              description="Where the module is listed; folders only organise the module list"
              data={folderOptions}
              allowDeselect={false}
              disabled={treeQuery.isLoading}
              error={treeQuery.isError ? 'Failed to load the module folders' : undefined}
              {...form.getInputProps('folder')}
            />
          )}
          <Textarea label="Description" {...form.getInputProps('description')} />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              loading={createModule.isPending || createFromTemplate.isPending}
              disabled={fromTemplate && selectedTemplate === null}
            >
              Create
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
