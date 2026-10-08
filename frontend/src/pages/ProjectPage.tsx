import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Center,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import {
  IconAdjustments,
  IconArrowDown,
  IconArrowUp,
  IconFolderPlus,
  IconHierarchy2,
  IconInfoCircle,
  IconLink,
  IconLock,
  IconLockOpen,
  IconPalette,
  IconPlus,
  IconTemplate,
  IconTrash,
  IconTrashX,
} from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAppActions } from '../actions/context';
import { ApiError } from '../api/client';
import {
  useModules,
  useModuleTree,
  useProject,
  useProjectReferences,
  useProjects,
  useRemoveLinkConfiguration,
  useRemoveView,
  useReorderModule,
} from '../api/queries';
import { attributeScopeLabel, REQUIRED_SCOPE_HINT } from '../api/applicability';
import { MODULE_TYPE_LABELS, moduleTypeOf } from '../api/moduleTypes';
import { AddAttributeModal } from '../components/AddAttributeModal';
import { AttributeModulesModal } from '../components/AttributeModulesModal';
import { GitStatusPanel } from '../components/GitStatusPanel';
import { LinkConfigurationModal } from '../components/LinkConfigurationModal';
import { ModuleTypeIcon } from '../components/ModuleTypeIcon';
import { ProjectLockBadge } from '../components/ProjectLockBadge';
import { describeLock } from '../components/projectLock';
import { moduleFolderPaths, treePosition } from '../components/moduleTree';
import { ProjectParentModal } from '../components/ProjectParentModal';
import { richTextToPlain } from '../components/richText';
import { ViewModal } from '../components/ViewModal';
import {
  isProjectStub,
  type AttributeDefinition,
  type LinkConfiguration,
  type Module,
  type View,
} from '../api/types';

/** A module of another (referenced) project, named "Project: Module". */
function CrossProjectModuleLabel({ projectId, moduleId, projectName }: { projectId: string; moduleId: string; projectName: string }) {
  const modulesQuery = useModules(projectId);
  const module = modulesQuery.data?.find((m) => m.id === moduleId);
  return (
    <Group gap={4} wrap="nowrap">
      <IconLink size={14} aria-label="Cross-project" />
      <Text size="sm">
        {projectName}: {module?.name ?? moduleId}
      </Text>
    </Group>
  );
}

/** The projects this project references (parent implicitly) and the projects referencing it. */
function ProjectReferencesCard({ projectId, onEdit, canEdit }: { projectId: string; onEdit: () => void; canEdit: boolean }) {
  const referencesQuery = useProjectReferences(projectId);
  const data = referencesQuery.data;
  return (
    <Card withBorder>
      <Group justify="space-between" mb="sm">
        <Title order={4}>References</Title>
        <Button size="xs" variant="default" leftSection={<IconLink size={14} />} onClick={onEdit} disabled={!canEdit}>
          Edit references
        </Button>
      </Group>
      <Text size="xs" c="dimmed" mb="sm">
        Link configurations may target modules of referenced projects (the parent is referenced
        implicitly). Cross-project links are stored in the referencing project.
      </Text>
      {referencesQuery.isLoading && <Loader size="sm" />}
      {referencesQuery.isError && (
        <Text c="red" size="sm">
          Failed to load references
          {referencesQuery.error instanceof Error ? `: ${referencesQuery.error.message}` : ''}
        </Text>
      )}
      {data && (
        <Stack gap="xs">
          <Group gap={4}>
            <Text size="sm">References:</Text>
            {data.references.length === 0 && (
              <Text size="sm" c="dimmed">
                none
              </Text>
            )}
            {data.references.map((ref) => (
              <Tooltip
                key={ref.id}
                label={
                  ref.error ??
                  `${ref.implicit ? 'Parent project (referenced implicitly)' : 'Referenced project'}; ${ref.link_configurations} link configuration(s) target it`
                }
                multiline
                w={320}
              >
                <Badge
                  variant={ref.implicit ? 'outline' : 'light'}
                  color={ref.error ? 'red' : undefined}
                  component={ref.error ? undefined : Link}
                  to={`/projects/${encodeURIComponent(ref.id)}`}
                  style={{ cursor: ref.error ? undefined : 'pointer' }}
                >
                  {ref.name}
                  {ref.implicit ? ' (parent)' : ''}
                </Badge>
              </Tooltip>
            ))}
          </Group>
          <Group gap={4}>
            <Text size="sm">Referenced by:</Text>
            {data.referenced_by.length === 0 && (
              <Text size="sm" c="dimmed">
                no project
              </Text>
            )}
            {data.referenced_by.map((ref) => {
              const links = ref.link_configurations.reduce((sum, c) => sum + c.links, 0);
              return (
                <Tooltip
                  key={ref.id}
                  label={
                    ref.link_configurations.length === 0
                      ? 'No link configuration targets this project'
                      : ref.link_configurations.map((c) => `${c.name}: ${c.links} link(s)`).join('; ')
                  }
                  multiline
                  w={320}
                >
                  <Badge
                    variant="light"
                    color="grape"
                    component={Link}
                    to={`/projects/${encodeURIComponent(ref.id)}`}
                    style={{ cursor: 'pointer' }}
                  >
                    {ref.name}
                    {links > 0 ? ` (${links} link${links === 1 ? '' : 's'})` : ''}
                  </Badge>
                </Tooltip>
              );
            })}
          </Group>
        </Stack>
      )}
    </Card>
  );
}

export function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const projectQuery = useProject(projectId);
  const projectsQuery = useProjects();
  const modulesQuery = useModules(projectId);
  const moduleTreeQuery = useModuleTree(projectId);
  const removeLinkConfiguration = useRemoveLinkConfiguration(projectId ?? '');
  const removeView = useRemoveView(projectId ?? '');
  const reorderModule = useReorderModule(projectId ?? '');
  const [addAttributeOpened, setAddAttributeOpened] = useState(false);
  const [scopedAttribute, setScopedAttribute] = useState<AttributeDefinition | null>(null);
  const [newLinkConfigOpened, setNewLinkConfigOpened] = useState(false);
  const [newViewOpened, setNewViewOpened] = useState(false);
  const [parentOpened, setParentOpened] = useState(false);
  const { run, isAvailable } = useAppActions();

  if (!projectId) return null;

  if (projectQuery.isLoading) {
    return (
      <Center h="60vh">
        <Loader />
      </Center>
    );
  }

  if (projectQuery.isError || !projectQuery.data) {
    return (
      <Alert icon={<IconInfoCircle size={16} />} color="red" title="Failed to load project">
        {projectQuery.error instanceof Error ? projectQuery.error.message : 'Unknown error'}
      </Alert>
    );
  }

  const project = projectQuery.data;
  // The project list reports the effective tree (a dangling/cyclic stored parent is top level).
  const projects = projectsQuery.data ?? [];
  const parentId = projects.find((p) => p.id === projectId)?.parent_project_id ?? null;
  const parent = parentId ? projects.find((p) => p.id === parentId) : undefined;
  const subprojects = projects.filter((p) => p.parent_project_id === projectId);
  const modulesById = new Map<string, Module>((modulesQuery.data ?? []).map((m) => [m.id, m]));
  const moduleLabel = (moduleId: string) => modulesById.get(moduleId)?.name ?? moduleId;
  // Modules are listed depth-first through their folders; Move up/down moves within the folder.
  const folderPaths = moduleFolderPaths(moduleTreeQuery.data);

  const handleRemoveLinkConfig = (config: LinkConfiguration) => {
    modals.openConfirmModal({
      title: 'Remove link configuration',
      children: (
        <Text size="sm">
          Are you sure you want to remove "{config.name}"? Existing links and their mirrored values
          will be deleted. This cannot be undone.
        </Text>
      ),
      labels: { confirm: 'Remove', cancel: 'Cancel' },
      confirmProps: { color: 'red' },
      onConfirm: () => {
        removeLinkConfiguration.mutate(config.id, {
          onSuccess: () => notifications.show({ message: `Removed "${config.name}"`, color: 'green' }),
          onError: (err) =>
            notifications.show({
              message: err instanceof ApiError ? err.message : 'Failed to remove link configuration',
              color: 'red',
            }),
        });
      },
    });
  };

  const handleRemoveView = (view: View) => {
    modals.openConfirmModal({
      title: 'Remove view',
      children: <Text size="sm">Are you sure you want to remove the view "{view.name}"?</Text>,
      labels: { confirm: 'Remove', cancel: 'Cancel' },
      confirmProps: { color: 'red' },
      onConfirm: () => {
        removeView.mutate(view.id, {
          onSuccess: () => notifications.show({ message: `Removed "${view.name}"`, color: 'green' }),
          onError: (err) =>
            notifications.show({
              message: err instanceof ApiError ? err.message : 'Failed to remove view',
              color: 'red',
            }),
        });
      },
    });
  };

  const handleReorderModule = (module: Module, direction: 'up' | 'down') => {
    reorderModule.mutate(
      { id: module.id, direction },
      {
        onError: (err) =>
          notifications.show({
            message: err instanceof ApiError ? err.message : 'Failed to move module',
            color: 'red',
          }),
      },
    );
  };

  // A locked project refuses every change (server-side); its editing controls are disabled.
  const locked = !!project.locked;

  const attributeLabel = (attrId: string) =>
    project.attributes.find((a) => a.id === attrId)?.name ?? attrId;

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <div>
          <Group gap="xs" wrap="nowrap">
            <Title order={2}>{project.name}</Title>
            <ProjectLockBadge lock={project.locked} />
          </Group>
          {parent && (
            <Text size="sm">
              Subproject of{' '}
              {isProjectStub(parent) ? (
                // A parent that cannot be loaded cannot be opened: no link.
                <Tooltip label={parent.error} multiline w={360} withArrow>
                  <Text span size="sm" c="dimmed">
                    {parent.name} (cannot be opened)
                  </Text>
                </Tooltip>
              ) : (
                <Anchor component={Link} to={`/projects/${parent.id}`}>
                  {parent.name}
                </Anchor>
              )}
            </Text>
          )}
          <Text c="dimmed">{project.description || 'No description'}</Text>
          {subprojects.length > 0 && (
            <Group gap={4} mt={4}>
              <Text size="sm">Subprojects:</Text>
              {subprojects.map((child) => (
                <Badge
                  key={child.id}
                  variant="light"
                  style={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/projects/${child.id}`)}
                >
                  {child.name}
                </Badge>
              ))}
            </Group>
          )}
        </div>
        <Group gap="xs" wrap="nowrap">
          <Button
            size="xs"
            variant="default"
            leftSection={<IconFolderPlus size={14} />}
            disabled={!isAvailable('project.newSubproject')}
            onClick={() => run('project.newSubproject')}
          >
            New subproject
          </Button>
          <Button
            size="xs"
            variant="default"
            leftSection={<IconHierarchy2 size={14} />}
            disabled={locked}
            onClick={() => setParentOpened(true)}
          >
            Change parent
          </Button>
          <Button
            size="xs"
            variant="default"
            leftSection={<IconLink size={14} />}
            disabled={!isAvailable('project.references')}
            onClick={() => run('project.references')}
          >
            References
          </Button>
          <Button
            size="xs"
            variant="default"
            leftSection={<IconTemplate size={14} />}
            onClick={() => run('file.saveAsTemplate')}
          >
            Save as template
          </Button>
          <Button
            size="xs"
            variant="default"
            leftSection={<IconPalette size={14} />}
            disabled={!isAvailable('view.rowStyles')}
            onClick={() => run('view.rowStyles')}
          >
            Row styles
          </Button>
          {locked ? (
            <Button
              size="xs"
              variant="default"
              leftSection={<IconLockOpen size={14} />}
              disabled={!isAvailable('project.unlock')}
              onClick={() => run('project.unlock')}
            >
              Unlock
            </Button>
          ) : (
            <Button
              size="xs"
              variant="default"
              leftSection={<IconLock size={14} />}
              disabled={!isAvailable('project.lock')}
              onClick={() => run('project.lock')}
            >
              Lock
            </Button>
          )}
          <Button
            size="xs"
            variant="default"
            color="red"
            leftSection={<IconTrashX size={14} />}
            disabled={!isAvailable('project.remove')}
            onClick={() => run('project.remove')}
          >
            Remove
          </Button>
        </Group>
      </Group>

      {project.locked && (
        <Alert icon={<IconLock size={16} />} color="orange" p="sm">
          {describeLock(project.locked)}
        </Alert>
      )}

      <GitStatusPanel projectId={projectId} />

      <ProjectReferencesCard
        projectId={projectId}
        canEdit={isAvailable('project.references')}
        onEdit={() => run('project.references')}
      />

      <Card withBorder>
        <Group justify="space-between" mb="sm">
          <Title order={4}>Attributes</Title>
          <Button size="xs" leftSection={<IconPlus size={14} />} disabled={locked} onClick={() => setAddAttributeOpened(true)}>
            Add attribute
          </Button>
        </Group>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Id</Table.Th>
              <Table.Th>Name</Table.Th>
              <Table.Th>Type</Table.Th>
              <Table.Th>Required</Table.Th>
              <Table.Th>Default</Table.Th>
              <Table.Th>Choices</Table.Th>
              <Table.Th>Modules</Table.Th>
              <Table.Th w={60} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {project.attributes.filter((attr) => !attr.system).map((attr) => (
              <Table.Tr key={attr.id}>
                <Table.Td>{attr.id}</Table.Td>
                <Table.Td>{attr.name}</Table.Td>
                <Table.Td>
                  <Badge variant="light">{attr.type}</Badge>
                </Table.Td>
                <Table.Td>{attr.required ? 'Yes' : 'No'}</Table.Td>
                <Table.Td>{attr.default === null || attr.default === undefined ? '—' : richTextToPlain(attr.default)}</Table.Td>
                <Table.Td>{attr.choices ? attr.choices.join(', ') : '—'}</Table.Td>
                <Table.Td>
                  {attributeScopeLabel(attr, moduleLabel)}
                </Table.Td>
                <Table.Td>
                  {attr.id !== 'object_text' && (
                    <Tooltip label={attr.required ? `${REQUIRED_SCOPE_HINT}; only optional ones can be limited` : 'Modules and module types it applies to'}>
                      <ActionIcon
                        variant="subtle"
                        aria-label={`Modules for ${attr.name}`}
                        // data-disabled, not disabled, so the tooltip still shows.
                        data-disabled={attr.required || locked || undefined}
                        aria-disabled={attr.required || locked}
                        onClick={() => {
                          if (!attr.required && !locked) setScopedAttribute(attr);
                        }}
                      >
                        <IconAdjustments size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      <Card withBorder>
        <Group justify="space-between" mb="sm">
          <Title order={4}>Link configurations</Title>
          <Button size="xs" leftSection={<IconPlus size={14} />} disabled={locked} onClick={() => setNewLinkConfigOpened(true)}>
            New link configuration
          </Button>
        </Group>
        {project.link_configurations.length === 0 && (
          <Text c="dimmed" size="sm">
            No link configurations yet. Create one to allow linking requirements between two modules.
          </Text>
        )}
        {project.link_configurations.length > 0 && (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Source module</Table.Th>
                <Table.Th>Target module</Table.Th>
                <Table.Th w={60} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {project.link_configurations.map((config) => (
                <Table.Tr key={config.id}>
                  <Table.Td>{config.name}</Table.Td>
                  <Table.Td>{moduleLabel(config.source_module_id)}</Table.Td>
                  <Table.Td>
                    {config.target_project_id ? (
                      <CrossProjectModuleLabel
                        projectId={config.target_project_id}
                        moduleId={config.target_module_id}
                        projectName={projects.find((p) => p.id === config.target_project_id)?.name ?? config.target_project_id}
                      />
                    ) : (
                      moduleLabel(config.target_module_id)
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Tooltip label="Remove">
                      <ActionIcon variant="subtle" color="red" disabled={locked} onClick={() => handleRemoveLinkConfig(config)}>
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>

      <Card withBorder>
        <Group justify="space-between" mb="sm">
          <Title order={4}>Views</Title>
          <Button size="xs" leftSection={<IconPlus size={14} />} disabled={locked} onClick={() => setNewViewOpened(true)}>
            New view
          </Button>
        </Group>
        <Text size="xs" c="dimmed" mb="sm">
          A view narrows which attributes show as table columns in a module — every requirement
          still has every attribute, a view just declutters what's displayed. Use "All attributes"
          in a module to see everything again.
        </Text>
        {project.views.length === 0 && (
          <Text c="dimmed" size="sm">
            No views yet. Create one to show only a subset of attributes in the requirements table.
          </Text>
        )}
        {project.views.length > 0 && (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Attributes</Table.Th>
                <Table.Th w={60} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {project.views.map((view) => (
                <Table.Tr key={view.id}>
                  <Table.Td>{view.name}</Table.Td>
                  <Table.Td>{view.attribute_ids.map(attributeLabel).join(', ')}</Table.Td>
                  <Table.Td>
                    <Tooltip label="Remove">
                      <ActionIcon variant="subtle" color="red" disabled={locked} onClick={() => handleRemoveView(view)}>
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>

      <Card withBorder>
        <Group justify="space-between" mb="sm">
          <Title order={4}>Modules</Title>
          <Button
            size="xs"
            leftSection={<IconPlus size={14} />}
            disabled={!isAvailable('project.newModule')}
            onClick={() => run('project.newModule')}
          >
            New module
          </Button>
        </Group>
        {modulesQuery.isLoading && <Loader size="sm" />}
        {modulesQuery.isError && (
          <Text c="red" size="sm">
            Failed to load modules
            {modulesQuery.error instanceof Error ? `: ${modulesQuery.error.message}` : ''}
          </Text>
        )}
        {modulesQuery.data?.length === 0 && (
          <Text c="dimmed" size="sm">
            No modules yet. Create one to start adding requirements.
          </Text>
        )}
        <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }}>
          {modulesQuery.data?.map((module) => {
            const position = treePosition(moduleTreeQuery.data, 'module', module.id);
            return (
            <Card
              key={module.id}
              withBorder
              padding="sm"
              style={{ cursor: 'pointer' }}
              onClick={() => navigate(`/projects/${projectId}/modules/${module.id}`)}
            >
              <Group gap="xs" justify="space-between" wrap="nowrap">
                <Group gap="xs">
                  <ModuleTypeIcon module={module} size={16} />
                  <Text fw={600}>{module.name}</Text>
                  <Badge size="sm" variant="outline">
                    {module.prefix}
                  </Badge>
                </Group>
                <Group gap={2} wrap="nowrap" onClick={(event) => event.stopPropagation()}>
                  <Tooltip label="Move up (within its folder)">
                    <ActionIcon
                      variant="subtle"
                      size="sm"
                      aria-label={`Move ${module.name} up`}
                      disabled={locked || !position || position.index === 0 || reorderModule.isPending}
                      onClick={() => handleReorderModule(module, 'up')}
                    >
                      <IconArrowUp size={14} />
                    </ActionIcon>
                  </Tooltip>
                  <Tooltip label="Move down (within its folder)">
                    <ActionIcon
                      variant="subtle"
                      size="sm"
                      aria-label={`Move ${module.name} down`}
                      disabled={
                        locked || !position || position.index >= position.siblingCount - 1 || reorderModule.isPending
                      }
                      onClick={() => handleReorderModule(module, 'down')}
                    >
                      <IconArrowDown size={14} />
                    </ActionIcon>
                  </Tooltip>
                </Group>
              </Group>
              <Text size="xs" c="dimmed">
                {MODULE_TYPE_LABELS[moduleTypeOf(module)]}
                {folderPaths.has(module.id) ? ` · in ${folderPaths.get(module.id)}` : ''}
              </Text>
              <Text size="sm" c="dimmed" lineClamp={2}>
                {module.description || 'No description'}
              </Text>
            </Card>
            );
          })}
        </SimpleGrid>
      </Card>

      <AddAttributeModal projectId={projectId} opened={addAttributeOpened} onClose={() => setAddAttributeOpened(false)} />
      <AttributeModulesModal
        projectId={projectId}
        attribute={scopedAttribute}
        onClose={() => setScopedAttribute(null)}
      />
      <LinkConfigurationModal
        projectId={projectId}
        opened={newLinkConfigOpened}
        onClose={() => setNewLinkConfigOpened(false)}
      />
      <ViewModal
        projectId={projectId}
        attributes={project.attributes}
        opened={newViewOpened}
        onClose={() => setNewViewOpened(false)}
      />
      <ProjectParentModal project={project} opened={parentOpened} onClose={() => setParentOpened(false)} />
    </Stack>
  );
}
