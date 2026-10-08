import { Button, Group, Modal, Select, Stack, Text, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useEffect } from 'react';
import { notifications } from '@mantine/notifications';
import { useModules, useModuleTree, useCreateLinkConfiguration, useProject, useProjects } from '../api/queries';
import { ApiError } from '../api/client';
import { LINK_TYPE_PAIRS, linkTypesAllowed, MODULE_TYPE_LABELS, moduleTypeOf } from '../api/moduleTypes';
import { isProjectStub, type Module } from '../api/types';
import { ModuleTypeIcon } from './ModuleTypeIcon';
import { groupedModuleOptions } from './moduleTree';

const ALLOWED_PAIRS_HINT = LINK_TYPE_PAIRS.map(
  ([source, target]) => `${MODULE_TYPE_LABELS[source]} → ${MODULE_TYPE_LABELS[target]}`,
).join(', ');

function moduleOption(module: Module, disabled = false) {
  return { value: module.id, label: `${module.name} (${MODULE_TYPE_LABELS[moduleTypeOf(module)]})`, disabled };
}

interface LinkConfigurationModalProps {
  projectId: string;
  opened: boolean;
  onClose: () => void;
}

interface FormValues {
  name: string;
  source_module_id: string;
  /** '' = this project; else a referenced project's id. */
  target_project_id: string;
  target_module_id: string;
}

/** Select value of "this project" (project ids are slugs and can never be empty). */
const THIS_PROJECT = '';

export function LinkConfigurationModal({ projectId, opened, onClose }: LinkConfigurationModalProps) {
  const modulesQuery = useModules(projectId);
  const projectQuery = useProject(projectId);
  const projectsQuery = useProjects();
  const createLinkConfiguration = useCreateLinkConfiguration(projectId);
  const form = useForm<FormValues>({
    initialValues: { name: '', source_module_id: '', target_project_id: THIS_PROJECT, target_module_id: '' },
    validate: {
      name: (value) => (value.trim().length === 0 ? 'Name is required' : null),
      source_module_id: (value) => (value.length === 0 ? 'Source module is required' : null),
      target_module_id: (value) => (value.length === 0 ? 'Target module is required' : null),
    },
  });

  const modules = modulesQuery.data ?? [];
  const modulesById = new Map(modules.map((m) => [m.id, m]));
  // The projects a configuration may target: this one and the referenced ones (the parent implicitly).
  const project = projectQuery.data;
  const projects = projectsQuery.data ?? [];
  const referenced = project
    ? [project.parent_project_id, ...(project.references ?? [])].filter(
        (id, index, all): id is string => !!id && id !== project.id && all.indexOf(id) === index,
      )
    : [];
  const projectChoices = [
    { value: THIS_PROJECT, label: 'This project' },
    ...referenced.map((id) => {
      const entry = projects.find((p) => p.id === id);
      return {
        value: id,
        label: entry ? (isProjectStub(entry) ? `${entry.name} (cannot be loaded)` : entry.name) : `${id} (missing)`,
        disabled: !entry || isProjectStub(entry),
      };
    }),
  ];
  const targetProjectId = form.values.target_project_id;
  const targetModulesQuery = useModules(targetProjectId === THIS_PROJECT ? undefined : targetProjectId);
  // Module folders group the pickers' options (a missing tree just lists the modules ungrouped).
  const treeQuery = useModuleTree(projectId);
  const targetTreeQuery = useModuleTree(targetProjectId === THIS_PROJECT ? undefined : targetProjectId);
  const targetTree = targetProjectId === THIS_PROJECT ? treeQuery.data : targetTreeQuery.data;
  const targetModules = targetProjectId === THIS_PROJECT ? modules : (targetModulesQuery.data ?? []);
  const targetModulesLoading = targetProjectId === THIS_PROJECT ? modulesQuery.isLoading : targetModulesQuery.isLoading;
  const targetModulesById = new Map(targetModules.map((m) => [m.id, m]));
  const source = modulesById.get(form.values.source_module_id);
  const target = targetModulesById.get(form.values.target_module_id);
  const targetInvalid = source !== undefined && target !== undefined && !linkTypesAllowed(moduleTypeOf(source), moduleTypeOf(target));
  // A new source can make the picked target invalid: clear it.
  useEffect(() => {
    if (targetInvalid) form.setFieldValue('target_module_id', '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetInvalid]);
  // Module types decide which modules may be linked (mirrors the server rule).
  const sourceOptions = groupedModuleOptions(treeQuery.data, modules, (m) =>
    moduleOption(m, !modules.some((t) => linkTypesAllowed(moduleTypeOf(m), moduleTypeOf(t)))),
  );
  const targetOptions = groupedModuleOptions(targetTree, targetModules, (m) =>
    moduleOption(m, source !== undefined && !linkTypesAllowed(moduleTypeOf(source), moduleTypeOf(m))),
  );
  const renderOption = (byId: Map<string, Module>) =>
    function ModuleOption({ option }: { option: { value: string; label: string } }) {
      return (
        <Group gap="xs" wrap="nowrap">
          <ModuleTypeIcon module={byId.get(option.value)} size={14} />
          {option.label}
        </Group>
      );
    };

  const handleSubmit = form.onSubmit((values) => {
    const body = {
      name: values.name,
      source_module_id: values.source_module_id,
      target_module_id: values.target_module_id,
      ...(values.target_project_id ? { target_project_id: values.target_project_id } : {}),
    };
    createLinkConfiguration.mutate(body, {
      onSuccess: () => {
        notifications.show({ message: `Link configuration "${values.name}" created`, color: 'green' });
        form.reset();
        onClose();
      },
      onError: (error) => {
        notifications.show({
          message: error instanceof ApiError ? error.message : 'Failed to create link configuration',
          color: 'red',
        });
      },
    });
  });

  return (
    <Modal opened={opened} onClose={onClose} title="New link configuration" centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          <TextInput label="Name" placeholder="Verifies" required {...form.getInputProps('name')} />
          <Select
            label="Source module"
            placeholder="Pick a module"
            data={sourceOptions}
            renderOption={renderOption(modulesById)}
            required
            disabled={modulesQuery.isLoading}
            error={modulesQuery.isError ? 'Failed to load modules' : undefined}
            {...form.getInputProps('source_module_id')}
          />
          <Select
            label="Target project"
            description="A referenced project (see the project's References) for a cross-project link"
            data={projectChoices}
            value={targetProjectId}
            onChange={(value) => {
              form.setFieldValue('target_project_id', value ?? THIS_PROJECT);
              form.setFieldValue('target_module_id', '');
            }}
            allowDeselect={false}
            disabled={projectQuery.isLoading}
          />
          <Select
            label="Target module"
            placeholder={source ? 'Pick a module' : 'Pick the source module first'}
            data={targetOptions}
            renderOption={renderOption(targetModulesById)}
            required
            disabled={targetModulesLoading || !source}
            {...form.getInputProps('target_module_id')}
            error={
              form.errors.target_module_id ??
              (targetProjectId !== THIS_PROJECT && targetModulesQuery.isError
                ? 'Failed to load the modules of that project'
                : undefined)
            }
          />
          <Text size="xs" c="dimmed">
            Allowed links: {ALLOWED_PAIRS_HINT}.
          </Text>
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={createLinkConfiguration.isPending}>
              Create
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
