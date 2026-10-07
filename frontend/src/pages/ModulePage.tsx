import {
  ActionIcon,
  Alert,
  Badge,
  Button,
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
import { notifications } from '@mantine/notifications';
import {
  IconChevronDown,
  IconChevronRight,
  IconClipboard,
  IconLink,
  IconPlus,
  IconArrowsMove,
  IconTrash,
} from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ActionMenuEntries } from '../actions/ActionMenuEntries';
import { useAppActions } from '../actions/context';
import { COLUMN_HEADER_MENU, REQUIREMENT_CONTEXT_MENU } from '../actions/registry';
import { applicableAttributes } from '../api/applicability';
import { api, ApiError } from '../api/client';
import {
  useModule,
  useProject,
  useRequirements,
  useUpdateRequirement,
  useViews,
} from '../api/queries';
import type { FindRequirementResult } from '../api/queries';
import { EditableAttributeCell } from '../components/EditableAttributeCell';
import { LinkModal } from '../components/LinkModal';
import { MoveRequirementModal } from '../components/MoveRequirementModal';
import { RequirementDetailPanel } from '../components/RequirementDetailPanel';
import { RequirementFormModal } from '../components/RequirementFormModal';
import { ResizableHeaderCell } from '../components/ResizableHeaderCell';
import { RowResizeHandle } from '../components/RowResizeHandle';
import {
  DEFAULT_ID_COLUMN_WIDTH,
  ID_COLUMN,
  defaultColumnWidth,
  moduleTableKey,
  useResizableColumns,
  useResizableRows,
} from '../hooks/columnLayout';
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

/** The Actions column is not resizable; it takes the table's remaining width, at least this much. */
const ACTIONS_COLUMN_MIN_WIDTH = 150;

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
  const updateRequirement = useUpdateRequirement(projectId ?? '', moduleId ?? '');
  // Selection, clipboard, active view and the detail panel are shared with
  // the menu bar and keyboard shortcuts through the action registry.
  const { state: appState, run, isAvailable } = useAppActions();
  const { commands } = appState;
  // Column widths, wrap toggles and row heights: per browser, per project +
  // module + view; a view's shared widths (project.json) are the defaults.
  const viewForColumns = (viewsQuery.data ?? []).find((v) => v.id === appState.activeViewId) ?? null;
  const tableKey = moduleTableKey(projectId ?? '', moduleId ?? '', viewForColumns?.id ?? null);
  const columns = useResizableColumns(tableKey, viewForColumns?.column_widths);
  const { tableRef, colRef } = columns;
  const rowSizes = useResizableRows(tableKey);
  const { tbodyRef } = rowSizes;

  const [addParentId, setAddParentId] = useState<string | null | undefined>(undefined);
  const [linkSource, setLinkSource] = useState<Requirement | null>(null);
  const [moveSource, setMoveSource] = useState<Requirement | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map());
  // A row to scroll to once it is rendered (its ancestors may just have been expanded).
  const scrollTargetRef = useRef<string | null>(null);

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
    scrollTargetRef.current = highlightId;

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

  // After every render: scroll to the pending row once it exists.
  useEffect(() => {
    const targetId = scrollTargetRef.current;
    if (!targetId) return;
    const row = rowRefs.current.get(targetId);
    if (!row) return;
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    scrollTargetRef.current = null;
  });

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

  // Only the attributes that apply to this module: table, views, detail panel and forms.
  const attributes = applicableAttributes(projectQuery.data.attributes, moduleId);
  const requirements = requirementsQuery.data;
  const module = moduleQuery.data;
  const rows = flattenTree(requirements, collapsed);
  const views = viewsQuery.data ?? [];
  const activeViewId = appState.activeViewId;
  const activeView = views.find((v) => v.id === activeViewId) ?? null;
  const visibleAttributes = activeView
    ? attributes.filter((a) => activeView.attribute_ids.includes(a.id))
    : attributes;
  const columnWidth = (key: string, fallback: number) => columns.widthOf(key, fallback);
  const tableMinWidth =
    columnWidth(ID_COLUMN, DEFAULT_ID_COLUMN_WIDTH) +
    visibleAttributes.reduce((sum, attr) => sum + columnWidth(attr.id, defaultColumnWidth(attr)), 0) +
    ACTIONS_COLUMN_MIN_WIDTH;
  const columnMenu = <ActionMenuEntries entries={COLUMN_HEADER_MENU} />;
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
        scrollTargetRef.current = targetId;
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

  // Fills the page area: the title and toolbar stay put, the table and the
  // detail panel each scroll on their own.
  return (
    <Stack gap="md" className="reqman-fill-page">
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

      <Group align="stretch" wrap="nowrap" gap="md" style={{ flex: 1, minHeight: 0 }}>
      {/* The table's own scroll container (both axes), so the sticky header row stays visible. */}
      <div className="reqman-table-scroll" style={{ flex: 1 }}>
        <Table
          ref={tableRef}
          className="reqman-resizable-table"
          striped
          highlightOnHover
          withTableBorder
          stickyHeader
          style={{ width: '100%', minWidth: tableMinWidth }}
        >
          <colgroup>
            <col ref={colRef(ID_COLUMN)} style={{ width: columnWidth(ID_COLUMN, DEFAULT_ID_COLUMN_WIDTH) }} />
            {visibleAttributes.map((attr) => (
              <col
                key={attr.id}
                ref={colRef(attr.id)}
                style={{ width: columnWidth(attr.id, defaultColumnWidth(attr)) }}
              />
            ))}
            {/* Actions: no width, so it takes whatever the resized columns leave. */}
            <col />
          </colgroup>
          <Table.Thead>
            <Table.Tr>
              <ResizableHeaderCell
                columnKey={ID_COLUMN}
                label="Id"
                defaultWidth={DEFAULT_ID_COLUMN_WIDTH}
                columns={columns}
                menuExtra={columnMenu}
              />
              {visibleAttributes.map((attr) => (
                <ResizableHeaderCell
                  key={attr.id}
                  columnKey={attr.id}
                  label={attr.name}
                  defaultWidth={defaultColumnWidth(attr)}
                  columns={columns}
                  menuExtra={columnMenu}
                />
              ))}
              <Table.Th>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody ref={tbodyRef}>
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
                data-row-height={rowSizes.heightOf(req.id)}
                style={{ cursor: 'pointer', ...rowSizes.rowStyle(req.id) }}
                onContextMenu={(e) => {
                  e.preventDefault();
                  commands.selectRequirement(req.id);
                  setContextMenu({ x: e.clientX, y: e.clientY, requirement: req });
                }}
              >
                <Table.Td data-column-key={ID_COLUMN}>
                  <div className="reqman-cell-clip" data-fade={columns.isSingleLine(ID_COLUMN) ? undefined : ''}>
                    <Text fw={600} truncate={columns.isSingleLine(ID_COLUMN) ? 'end' : undefined}>
                      {req.id}
                    </Text>
                  </div>
                  <RowResizeHandle rowId={req.id} rows={rowSizes} focusable />
                </Table.Td>
                {visibleAttributes.map((attr, attrIdx) => (
                  <Table.Td key={attr.id} data-column-key={attr.id}>
                    <div className="reqman-cell-clip" data-fade={columns.isSingleLine(attr.id) ? undefined : ''}>
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
                            wrap={!columns.isSingleLine(attr.id)}
                          />
                        </div>
                      </Group>
                    </div>
                    <RowResizeHandle rowId={req.id} rows={rowSizes} />
                  </Table.Td>
                ))}
                <Table.Td>
                  <div className="reqman-cell-clip">
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
                  </div>
                  <RowResizeHandle rowId={req.id} rows={rowSizes} />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </div>

      {appState.detailPanelOpen && (
        <RequirementDetailPanel
          projectId={projectId}
          moduleId={moduleId}
          attributes={attributes}
          requirement={selectedRequirement}
          onSave={handleCellSave}
          saving={updateRequirement.isPending}
          onNavigateToLinked={handleNavigateToLinked}
          onClose={() => run('view.toggleDetailPanel')}
        />
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

