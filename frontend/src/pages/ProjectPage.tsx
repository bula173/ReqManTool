import {
  ActionIcon,
  Alert,
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
import { IconInfoCircle, IconPlus, IconTrash } from '@tabler/icons-react';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useModules, useProject, useRemoveLinkConfiguration, useRemoveView } from '../api/queries';
import { AddAttributeModal } from '../components/AddAttributeModal';
import { LinkConfigurationModal } from '../components/LinkConfigurationModal';
import { NewModuleModal } from '../components/NewModuleModal';
import { ViewModal } from '../components/ViewModal';
import type { LinkConfiguration, Module, View } from '../api/types';

export function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const projectQuery = useProject(projectId);
  const modulesQuery = useModules(projectId);
  const removeLinkConfiguration = useRemoveLinkConfiguration(projectId ?? '');
  const removeView = useRemoveView(projectId ?? '');
  const [addAttributeOpened, setAddAttributeOpened] = useState(false);
  const [newModuleOpened, setNewModuleOpened] = useState(false);
  const [newLinkConfigOpened, setNewLinkConfigOpened] = useState(false);
  const [newViewOpened, setNewViewOpened] = useState(false);

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
  const modulesById = new Map<string, Module>((modulesQuery.data ?? []).map((m) => [m.id, m]));
  const moduleLabel = (moduleId: string) => modulesById.get(moduleId)?.name ?? moduleId;

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

  const attributeLabel = (attrId: string) =>
    project.attributes.find((a) => a.id === attrId)?.name ?? attrId;

  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>{project.name}</Title>
        <Text c="dimmed">{project.description || 'No description'}</Text>
      </div>

      <Card withBorder>
        <Group justify="space-between" mb="sm">
          <Title order={4}>Attributes</Title>
          <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setAddAttributeOpened(true)}>
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
                <Table.Td>{attr.default === null || attr.default === undefined ? '—' : String(attr.default)}</Table.Td>
                <Table.Td>{attr.choices ? attr.choices.join(', ') : '—'}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      <Card withBorder>
        <Group justify="space-between" mb="sm">
          <Title order={4}>Link configurations</Title>
          <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setNewLinkConfigOpened(true)}>
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
                  <Table.Td>{moduleLabel(config.target_module_id)}</Table.Td>
                  <Table.Td>
                    <Tooltip label="Remove">
                      <ActionIcon variant="subtle" color="red" onClick={() => handleRemoveLinkConfig(config)}>
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
          <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setNewViewOpened(true)}>
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
                      <ActionIcon variant="subtle" color="red" onClick={() => handleRemoveView(view)}>
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
          <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setNewModuleOpened(true)}>
            New module
          </Button>
        </Group>
        {modulesQuery.isLoading && <Loader size="sm" />}
        {modulesQuery.isError && (
          <Text c="red" size="sm">
            Failed to load modules
          </Text>
        )}
        {modulesQuery.data?.length === 0 && (
          <Text c="dimmed" size="sm">
            No modules yet. Create one to start adding requirements.
          </Text>
        )}
        <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }}>
          {modulesQuery.data?.map((module) => (
            <Card
              key={module.id}
              withBorder
              padding="sm"
              style={{ cursor: 'pointer' }}
              onClick={() => navigate(`/projects/${projectId}/modules/${module.id}`)}
            >
              <Group gap="xs">
                <Text fw={600}>{module.name}</Text>
                <Badge size="sm" variant="outline">
                  {module.prefix}
                </Badge>
              </Group>
              <Text size="sm" c="dimmed" lineClamp={2}>
                {module.description || 'No description'}
              </Text>
            </Card>
          ))}
        </SimpleGrid>
      </Card>

      <AddAttributeModal projectId={projectId} opened={addAttributeOpened} onClose={() => setAddAttributeOpened(false)} />
      <NewModuleModal projectId={newModuleOpened ? projectId : null} onClose={() => setNewModuleOpened(false)} />
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
    </Stack>
  );
}
