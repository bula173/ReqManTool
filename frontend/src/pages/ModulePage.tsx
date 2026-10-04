import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Center,
  Group,
  Loader,
  Menu,
  Select,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import {
  IconChevronDown,
  IconChevronRight,
  IconClipboard,
  IconDownload,
  IconFileExport,
  IconFileImport,
  IconLink,
  IconPlus,
  IconArrowsMove,
  IconTrash,
  IconX,
} from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ActionMenuEntries } from '../actions/ActionMenuEntries';
import { useAppActions } from '../actions/context';
import { REQUIREMENT_CONTEXT_MENU } from '../actions/registry';
import { api, ApiError } from '../api/client';
import {
  useModule,
  useProject,
  useRequirements,
  usePurgeModule,
  useUpdateRequirement,
  useViews,
} from '../api/queries';
import type { FindRequirementResult } from '../api/queries';
import { EditableAttributeCell } from '../components/EditableAttributeCell';
import { LinkModal } from '../components/LinkModal';
import { MoveRequirementModal } from '../components/MoveRequirementModal';
import { RequirementFormModal } from '../components/RequirementFormModal';
import type { Requirement } from '../api/types';

interface TreeRow {
  requirement: Requirement;
  depth: number;
  hasChildren: boolean;
}

function flattenTree(requirements: Requirement[], collapsed: Set<string>): TreeRow[] {
  const childrenOf = new Map<string | null, Requirement[]>();
  for (const req of requirements) {
    const key = req.parent_id;
    if (!childrenOf.has(key)) childrenOf.set(key, []);
    childrenOf.get(key)!.push(req);
  }
  for (const children of childrenOf.values()) {
    children.sort((a, b) => a.order - b.order);
  }

  const rows: TreeRow[] = [];
  const walk = (parentId: string | null, depth: number) => {
    for (const req of childrenOf.get(parentId) ?? []) {
      const hasChildren = (childrenOf.get(req.id) ?? []).length > 0;
      rows.push({ requirement: req, depth, hasChildren });
      if (hasChildren && !collapsed.has(req.id)) {
        walk(req.id, depth + 1);
      }
    }
  };
  walk(null, 0);
  return rows;
}

function ancestorIds(requirements: Requirement[], requirementId: string): string[] {
  const byId = new Map(requirements.map((r) => [r.id, r]));
  const ancestors: string[] = [];
  let current = byId.get(requirementId)?.parent_id ?? null;
  while (current) {
    ancestors.push(current);
    current = byId.get(current)?.parent_id ?? null;
  }
  return ancestors;
}

interface ContextMenuState {
  x: number;
  y: number;
  requirement: Requirement;
}

export function ModulePage() {
  const { projectId, moduleId } = useParams<{ projectId: string; moduleId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectQuery = useProject(projectId);
  const moduleQuery = useModule(projectId, moduleId);
  const requirementsQuery = useRequirements(projectId, moduleId);
  const viewsQuery = useViews(projectId);
  const purgeModule = usePurgeModule(projectId ?? '', moduleId ?? '');
  const updateRequirement = useUpdateRequirement(projectId ?? '', moduleId ?? '');
  // Selection, clipboard, active view and the detail panel are shared with
  // the menu bar and keyboard shortcuts through the action registry.
  const { state: appState, run, isAvailable } = useAppActions();
  const { commands } = appState;

  const [addParentId, setAddParentId] = useState<string | null | undefined>(undefined);
  const [linkSource, setLinkSource] = useState<Requirement | null>(null);
  const [moveSource, setMoveSource] = useState<Requirement | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map());

  const highlightId = searchParams.get('highlight');

  useEffect(() => {
    if (!highlightId || !requirementsQuery.data) return;
    if (!requirementsQuery.data.some((r) => r.id === highlightId)) return;

    setCollapsed((prev) => {
      const next = new Set(prev);
      for (const ancestorId of ancestorIds(requirementsQuery.data!, highlightId)) {
        next.delete(ancestorId);
      }
      return next;
    });
    commands.selectRequirement(highlightId);

    const row = rowRefs.current.get(highlightId);
    row?.scrollIntoView({ behavior: 'smooth', block: 'center' });

    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('highlight');
        return next;
      },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightId, requirementsQuery.data]);

  if (!projectId || !moduleId) return null;

  const isLoading = projectQuery.isLoading || moduleQuery.isLoading || requirementsQuery.isLoading;
  const error = projectQuery.error ?? moduleQuery.error ?? requirementsQuery.error;

  if (isLoading) {
    return (
      <Center h="60vh">
        <Loader />
      </Center>
    );
  }

  if (error || !projectQuery.data || !moduleQuery.data || !requirementsQuery.data) {
    return (
      <Alert color="red" title="Failed to load module">
        {error instanceof ApiError || error instanceof Error ? error.message : 'Unknown error'}
      </Alert>
    );
  }

  const attributes = projectQuery.data.attributes;
  const requirements = requirementsQuery.data;
  const module = moduleQuery.data;
  const rows = flattenTree(requirements, collapsed);
  const views = viewsQuery.data ?? [];
  const activeViewId = appState.activeViewId;
  const activeView = views.find((v) => v.id === activeViewId) ?? null;
  const visibleAttributes = activeView
    ? attributes.filter((a) => activeView.attribute_ids.includes(a.id))
    : attributes;
  const selectedId = appState.selectedRequirement?.id ?? null;
  const selectedRequirement = requirements.find((r) => r.id === selectedId) ?? null;

  const toggleExpanded = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleNavigateToLinked = async (targetId: string) => {
    try {
      const result = await api.get<FindRequirementResult>(`/projects/${projectId}/requirements/${targetId}`);
      if (result.module_id === moduleId) {
        setCollapsed((prev) => {
          const next = new Set(prev);
          for (const ancestorId of ancestorIds(requirements, targetId)) next.delete(ancestorId);
          return next;
        });
        commands.selectRequirement(targetId);
        rowRefs.current.get(targetId)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      } else {
        navigate(`/projects/${projectId}/modules/${result.module_id}?highlight=${targetId}`);
      }
    } catch (err) {
      notifications.show({
        message: err instanceof ApiError ? err.message : 'Failed to locate linked requirement',
        color: 'red',
      });
    }
  };

  const handleCellSave = (requirement: Requirement, attrId: string, value: unknown) => {
    updateRequirement.mutate(
      { id: requirement.id, attributes: { [attrId]: value } },
      {
        onSuccess: () => notifications.show({ message: `${requirement.id} updated`, color: 'green' }),
        onError: (err) =>
          notifications.show({
            message: err instanceof ApiError ? err.message : 'Failed to update requirement',
            color: 'red',
          }),
      },
    );
  };

  const handlePurge = () => {
    modals.openConfirmModal({
      title: 'Purge module',
      children: (
        <Text size="sm">
          Are you sure you want to delete ALL requirements in "{module.name}"? This cannot be undone.
        </Text>
      ),
      labels: { confirm: 'Purge', cancel: 'Cancel' },
      confirmProps: { color: 'red' },
      onConfirm: () => {
        purgeModule.mutate(undefined, {
          onSuccess: (result) => notifications.show({ message: `Purged ${result.purged} requirement(s)`, color: 'green' }),
          onError: (err) =>
            notifications.show({
              message: err instanceof ApiError ? err.message : 'Failed to purge module',
              color: 'red',
            }),
        });
      },
    });
  };

  return (
    <Stack gap="md">
      <div>
        <Group gap="xs">
          <Title order={2}>{module.name}</Title>
          <Badge variant="outline">{module.prefix}</Badge>
        </Group>
        <Text c="dimmed">{module.description || 'No description'}</Text>
      </div>

      <Group>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setAddParentId(null)}>
          Add Requirement
        </Button>
        <Button
          variant="default"
          leftSection={<IconClipboard size={16} />}
          disabled={!isAvailable('requirement.pasteAtRoot')}
          onClick={() => run('requirement.pasteAtRoot')}
        >
          Paste at root
        </Button>
        <Button color="red" variant="light" leftSection={<IconTrash size={16} />} onClick={handlePurge}>
          Purge Module
        </Button>
        <Button
          variant="default"
          leftSection={<IconFileExport size={16} />}
          disabled={!isAvailable('file.export.reqif')}
          onClick={() => run('file.export.reqif')}
        >
          Export ReqIF
          <IconDownload size={14} style={{ marginLeft: 6 }} />
        </Button>
        <Button
          variant="default"
          leftSection={<IconFileExport size={16} />}
          disabled={!isAvailable('file.export.csv')}
          onClick={() => run('file.export.csv')}
        >
          Export CSV
          <IconDownload size={14} style={{ marginLeft: 6 }} />
        </Button>
        <Button variant="default" leftSection={<IconFileImport size={16} />} onClick={() => run('file.importCsv')}>
          Import CSV
        </Button>
        <Button
          variant="default"
          leftSection={<IconFileExport size={16} />}
          disabled={!isAvailable('file.export.xlsx')}
          onClick={() => run('file.export.xlsx')}
        >
          Export XLSX
          <IconDownload size={14} style={{ marginLeft: 6 }} />
        </Button>
        <Button
          variant="default"
          leftSection={<IconFileExport size={16} />}
          disabled={!isAvailable('file.export.docx')}
          onClick={() => run('file.export.docx')}
        >
          Export DOCX
          <IconDownload size={14} style={{ marginLeft: 6 }} />
        </Button>
        <Select
          placeholder="All attributes"
          data={views.map((v) => ({ value: v.id, label: v.name }))}
          value={activeViewId}
          onChange={commands.setActiveView}
          clearable
          w={200}
          ml="auto"
        />
      </Group>

      <Group align="flex-start" wrap="nowrap" gap="md">
      <Table.ScrollContainer minWidth={700} style={{ flex: 1 }}>
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Id</Table.Th>
              {visibleAttributes.map((attr) => (
                <Table.Th key={attr.id}>{attr.name}</Table.Th>
              ))}
              <Table.Th w={200}>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={visibleAttributes.length + 2}>
                  <Text c="dimmed" ta="center" py="md">
                    No requirements yet.
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
            {rows.map(({ requirement: req, depth, hasChildren }) => (
              <Table.Tr
                key={req.id}
                ref={(el) => {
                  if (el) rowRefs.current.set(req.id, el);
                  else rowRefs.current.delete(req.id);
                }}
                onClick={() => commands.selectRequirement(req.id)}
                bg={selectedId === req.id ? 'var(--mantine-color-blue-light)' : undefined}
                style={{ cursor: 'pointer' }}
                onContextMenu={(e) => {
                  e.preventDefault();
                  commands.selectRequirement(req.id);
                  setContextMenu({ x: e.clientX, y: e.clientY, requirement: req });
                }}
              >
                <Table.Td>
                  <Text fw={600}>{req.id}</Text>
                </Table.Td>
                {visibleAttributes.map((attr, attrIdx) => (
                  <Table.Td key={attr.id}>
                    <Group gap={4} wrap="nowrap" pl={attrIdx === 0 ? depth * 20 : 0}>
                      {attrIdx === 0 && hasChildren && (
                        <ActionIcon variant="subtle" size="sm" onClick={() => toggleExpanded(req.id)}>
                          {collapsed.has(req.id) ? (
                            <IconChevronRight size={14} />
                          ) : (
                            <IconChevronDown size={14} />
                          )}
                        </ActionIcon>
                      )}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <EditableAttributeCell
                          attr={attr}
                          value={req.attributes[attr.id]}
                          onSave={(value) => handleCellSave(req, attr.id, value)}
                          disabled={updateRequirement.isPending}
                          projectId={projectId}
                          onNavigateToLinked={handleNavigateToLinked}
                        />
                      </div>
                    </Group>
                  </Table.Td>
                ))}
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    <Tooltip label="Add child">
                      <ActionIcon variant="subtle" onClick={() => setAddParentId(req.id)}>
                        <IconPlus size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Move">
                      <ActionIcon variant="subtle" onClick={() => setMoveSource(req)}>
                        <IconArrowsMove size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Link">
                      <ActionIcon variant="subtle" onClick={() => setLinkSource(req)}>
                        <IconLink size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Remove">
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        onClick={() => run('requirement.delete', { requirement: req })}
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      {appState.detailPanelOpen && (
      <Card withBorder w={320} style={{ flexShrink: 0 }}>
        <Group justify="space-between" mb="sm" wrap="nowrap">
          <Title order={5}>Requirement details</Title>
          <Tooltip label="Hide the detail panel (View › Requirement detail panel)">
            <ActionIcon
              variant="subtle"
              size="sm"
              aria-label="Hide requirement details"
              onClick={() => run('view.toggleDetailPanel')}
            >
              <IconX size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
        {!selectedRequirement && (
          <Text c="dimmed" size="sm">
            Select a requirement to see its full details.
          </Text>
        )}
        {selectedRequirement && (
          <Stack gap="sm">
            <div>
              <Text size="xs" c="dimmed">
                Id
              </Text>
              <Text fw={600}>{selectedRequirement.id}</Text>
            </div>
            {attributes.map((attr) => (
              <div key={attr.id}>
                <Text size="xs" c="dimmed">
                  {attr.name}
                </Text>
                <EditableAttributeCell
                  attr={attr}
                  value={selectedRequirement.attributes[attr.id]}
                  onSave={(value) => handleCellSave(selectedRequirement, attr.id, value)}
                  disabled={attr.system ? undefined : updateRequirement.isPending}
                  projectId={projectId}
                  onNavigateToLinked={handleNavigateToLinked}
                />
              </div>
            ))}
          </Stack>
        )}
      </Card>
      )}
      </Group>

      <Menu
        opened={contextMenu !== null}
        onClose={() => setContextMenu(null)}
        withinPortal
        shadow="md"
      >
        <Menu.Target>
          <div
            style={{
              position: 'fixed',
              top: contextMenu?.y ?? 0,
              left: contextMenu?.x ?? 0,
              width: 1,
              height: 1,
            }}
          />
        </Menu.Target>
        <Menu.Dropdown>
          {contextMenu && (
            <ActionMenuEntries entries={REQUIREMENT_CONTEXT_MENU} targets={{ requirement: contextMenu.requirement }} />
          )}
        </Menu.Dropdown>
      </Menu>

      <RequirementFormModal
        projectId={projectId}
        moduleId={moduleId}
        attributes={attributes}
        opened={addParentId !== undefined}
        parentId={addParentId ?? null}
        onClose={() => setAddParentId(undefined)}
      />
      <LinkModal
        projectId={projectId}
        moduleId={moduleId}
        source={linkSource}
        onClose={() => setLinkSource(null)}
      />
      <MoveRequirementModal
        projectId={projectId}
        moduleId={moduleId}
        source={moveSource}
        allRequirements={requirements}
        onClose={() => setMoveSource(null)}
      />
    </Stack>
  );
}

