import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Center,
  Group,
  Loader,
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
  IconPencil,
  IconTrash,
} from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { useAppActions } from '../actions/context';
import type { LinkEntry } from '../actions/links';
import {
  COLUMN_HEADER_EDIT_MENU,
  COLUMN_HEADER_VIEW_MENU,
  LINK_CONTEXT_MENU,
  LINK_NAVIGATION_MENU,
  REQUIREMENT_CONTEXT_MENU,
  REQUIREMENT_NAVIGATION_MENU,
  canChangeStatusOnly,
  isModuleLockedByOther,
  isModuleReadOnly,
  moduleLockOf,
  moduleNeedsCheckout,
  statusAttributeId,
} from '../actions/registry';
import { contextMenuTargetProps } from '../actions/shortcuts';
import { applicableAttributes, applicableViews } from '../api/applicability';
import { ApiError } from '../api/client';
import { moduleTypeOf } from '../api/moduleTypes';
import {
  useModule,
  useProject,
  useRequirements,
  useUpdateRequirement,
  useVerificationStatuses,
  useViews,
} from '../api/queries';
import { EditableAttributeCell } from '../components/EditableAttributeCell';
import { ModuleLockBadge } from '../components/ModuleLockBadge';
import { ProjectLockBadge } from '../components/ProjectLockBadge';
import { ProjectRoleBadge } from '../components/ProjectRoleBadge';
import { RequirementDetailPanel } from '../components/RequirementDetailPanel';
import { StatusBadge } from '../components/ReportParts';
import { ResizableHeaderCell, type ColumnMenuKind } from '../components/ResizableHeaderCell';
import { RowResizeHandle } from '../components/RowResizeHandle';
import { useRowAppearance } from '../components/rowStyles';
import {
  hasRequirementDragData,
  readRequirementDragData,
  setRequirementDragData,
} from '../components/requirementDrag';
import { useDropLink } from '../hooks/useDropLink';
import { RightSidePanel } from '../layout/SidePanelLayout';
import {
  DEFAULT_ID_COLUMN_WIDTH,
  DEFAULT_VERIFICATION_COLUMN_WIDTH,
  ID_COLUMN,
  VERIFICATION_COLUMN,
  defaultColumnWidth,
  moduleTableKey,
  treeColumnKey,
  useResizableColumns,
  useResizableRows,
} from '../hooks/columnLayout';
import type { AttributeDefinition, Requirement } from '../api/types';

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

export function ModulePage() {
  const { projectId, moduleId } = useParams<{ projectId: string; moduleId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectQuery = useProject(projectId);
  const moduleQuery = useModule(projectId, moduleId);
  const requirementsQuery = useRequirements(projectId, moduleId);
  const viewsQuery = useViews(projectId);
  const updateRequirement = useUpdateRequirement(projectId ?? '', moduleId ?? '');
  // The computed Verification status (requirements modules only): the optional
  // table column and the detail panel. Read-only; refetched after any change.
  const isRequirementsModule = moduleQuery.data !== undefined && moduleTypeOf(moduleQuery.data) === 'requirements';
  const verificationQuery = useVerificationStatuses(projectId, moduleId, isRequirementsModule);
  // Selection, clipboard, active view and the detail panel are shared with
  // the menu bar and keyboard shortcuts through the action registry.
  const { state: appState, run, isAvailable, openMenu } = useAppActions();
  const { commands } = appState;
  // Column layout: a named view's saved order, widths and wrap (project.json)
  // with this browser's unsaved changes on top; "All attributes" and row
  // heights are per browser (per project + module + view).
  const viewForColumns = (viewsQuery.data ?? []).find((v) => v.id === appState.activeViewId) ?? null;
  const tableKey = moduleTableKey(projectId ?? '', moduleId ?? '', viewForColumns?.id ?? null);
  const columns = useResizableColumns(tableKey, viewForColumns);
  const { tableRef, colRef } = columns;
  const rowSizes = useResizableRows(tableKey);
  const { tbodyRef } = rowSizes;
  // Row styling rules (font/background color, bold) by enum attribute value.
  const rowAppearance = useRowAppearance(projectQuery.data?.row_styles, projectQuery.data?.attributes);

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map());
  // A row to scroll to once it is rendered (its ancestors may just have been expanded).
  const scrollTargetRef = useRef<string | null>(null);

  const highlightId = searchParams.get('highlight');

  // Drag-and-drop linking: a requirement row (of this or another tab or
  // window) dropped on a row of this module links the two.
  const dropLink = useDropLink(projectId ?? '');
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  useEffect(() => {
    const clear = () => setDropTargetId(null);
    window.addEventListener('dragend', clear);
    window.addEventListener('drop', clear);
    return () => {
      window.removeEventListener('dragend', clear);
      window.removeEventListener('drop', clear);
    };
  }, []);

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
  const attributes = applicableAttributes(projectQuery.data.attributes, moduleQuery.data);
  const requirements = requirementsQuery.data;
  const module = moduleQuery.data;
  const rows = flattenTree(requirements, collapsed);
  // Only the views that apply to this module are offered.
  const views = applicableViews(viewsQuery.data ?? [], moduleId);
  const activeViewId = appState.activeViewId;
  const activeView = views.find((v) => v.id === activeViewId) ?? null;
  // The attribute columns: a named view's in the view's order, else the
  // project's; then this browser's (unsaved) order (dragged headers, Move
  // column left/right). Id stays first.
  const attributeById = new Map(attributes.map((a) => [a.id, a]));
  const visibleAttributes = columns.ordered(
    activeView
      ? activeView.attribute_ids.flatMap((id) => {
          const attr = attributeById.get(id);
          return attr ? [attr] : [];
        })
      : attributes,
    (a) => a.id,
  );
  const columnOrder = columns.orderOf(visibleAttributes.map((a) => a.id));
  // The hierarchy indent and expand arrow: in the Object Text column, wherever it is.
  const treeColumn = treeColumnKey(visibleAttributes.map((a) => a.id));
  const unsavedLayout = columns.viewDraft?.dirty === true;
  const columnWidth = (key: string, fallback: number) => columns.widthOf(key, fallback);
  const showVerification = isRequirementsModule && appState.verificationColumn;
  const statuses = verificationQuery.data?.statuses;
  const verificationOf = (requirementId: string) => ({
    status: statuses?.[requirementId] ?? null,
    loading: verificationQuery.isLoading,
    error: verificationQuery.isError,
  });
  const tableMinWidth =
    columnWidth(ID_COLUMN, DEFAULT_ID_COLUMN_WIDTH) +
    visibleAttributes.reduce((sum, attr) => sum + columnWidth(attr.id, defaultColumnWidth(attr)), 0) +
    (showVerification ? columnWidth(VERIFICATION_COLUMN, DEFAULT_VERIFICATION_COLUMN_WIDTH) : 0) +
    ACTIONS_COLUMN_MIN_WIDTH;
  // Column header menus: left click / ⋮ = view menu, right click = editing menu.
  const openColumnMenu = (
    columnKey: string,
    label: string,
    attribute: AttributeDefinition | null,
    kind: ColumnMenuKind,
    position: { x: number; y: number },
  ) =>
    openMenu({
      ...position,
      label,
      entries: kind === 'edit' ? COLUMN_HEADER_EDIT_MENU : COLUMN_HEADER_VIEW_MENU,
      targets: { column: { columnKey, label, attribute, columns, order: columnOrder } },
    });
  // Link badges: left click = go to, right click = go to / remove link.
  const openLinkMenu = (
    requirement: Requirement,
    configId: string,
    link: LinkEntry,
    kind: 'navigate' | 'edit',
    position: { x: number; y: number },
  ) =>
    openMenu({
      ...position,
      label: `${link.direction === 'out' ? '>' : '<'} ${link.otherId}`,
      entries: kind === 'edit' ? LINK_CONTEXT_MENU : LINK_NAVIGATION_MENU,
      targets: { link: { requirement, configId, ...link }, requirement },
    });
  const selectedId = appState.selectedRequirement?.id ?? null;
  const selectedRequirement = requirements.find((r) => r.id === selectedId) ?? null;
  // A locked project, one the user may not change (viewer), or (server edition) a module another user
  // checked out refuses every change (server-side): no inline editing, no drop-to-link, no row actions.
  const locked = isModuleReadOnly(appState, projectId, moduleId);
  // Server edition: a reviewer may still change the status attribute (sign-off); the server decides each change.
  const statusAttrId = statusAttributeId(visibleAttributes);
  const statusEditable = !locked || canChangeStatusOnly(appState, projectId, moduleId);
  // Server edition: the module's edit lock (null in the local edition).
  const moduleLock = moduleLockOf(appState, projectId, moduleId);
  const lockedByOther = isModuleLockedByOther(appState, projectId, moduleId);
  const needsCheckout = moduleNeedsCheckout(appState, projectId, moduleId);
  const moduleTarget = { module: { projectId, module, moduleIds: [], position: null } };

  const toggleExpanded = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // The shared "go to requirement" command (it reveals the row via `?highlight=`).
  const handleNavigateToLinked = (targetId: string) => {
    commands.goToRequirement(targetId).catch((err) =>
      notifications.show({
        message: err instanceof ApiError ? err.message : 'Failed to locate linked requirement',
        color: 'red',
      }),
    );
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
          <ProjectLockBadge lock={projectQuery.data.locked} />
          <ModuleLockBadge lock={moduleLock} userId={appState.userId} />
          <ProjectRoleBadge projectId={projectId} />
        </Group>
        <Text c="dimmed">{module.description || 'No description'}</Text>
      </div>
      {lockedByOther && moduleLock && (
        <Alert color="orange" p="xs">
          <Text size="sm">
            {moduleLock.user_name} has this module checked out for editing
            {moduleLock.reason ? ` (${moduleLock.reason})` : ''}: it is read-only for you until they release it.
          </Text>
        </Alert>
      )}
      {needsCheckout && (
        <Alert color="blue" p="xs">
          <Text size="sm">This server requires checking a module out before changing it.</Text>
        </Alert>
      )}

      <Group>
        <Button
          leftSection={<IconPlus size={16} />}
          disabled={!isAvailable('requirement.addTopLevel')}
          onClick={() => run('requirement.addTopLevel')}
        >
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
        {appState.access.serverMode && (
          <Group gap="xs">
            {isAvailable('module.checkOut', moduleTarget) && (
              <Button variant="default" onClick={() => run('module.checkOut', moduleTarget)}>
                Check out for editing
              </Button>
            )}
            {isAvailable('module.release', moduleTarget) && (
              <Button variant="default" onClick={() => run('module.release', moduleTarget)}>
                Release
              </Button>
            )}
            {isAvailable('module.breakLock', moduleTarget) && (
              <Button variant="default" color="red" onClick={() => run('module.breakLock', moduleTarget)}>
                Break lock
              </Button>
            )}
          </Group>
        )}
        <Group gap="xs" ml="auto" wrap="nowrap">
          {unsavedLayout && (
            <>
              <Tooltip label="This browser shows column changes (order, widths, wrap) not saved to the view">
                <Badge color="orange" variant="light">
                  Unsaved layout
                </Badge>
              </Tooltip>
              <Button
                size="xs"
                variant="light"
                disabled={!isAvailable('view.saveLayout')}
                onClick={() => run('view.saveLayout')}
              >
                Save to view
              </Button>
              <Button size="xs" variant="subtle" onClick={() => run('view.revertLayout')}>
                Revert
              </Button>
            </>
          )}
          <Select
            placeholder="All attributes"
            aria-label="View"
            data={views.map((v) => ({ value: v.id, label: v.name }))}
            value={activeViewId}
            onChange={commands.setActiveView}
            clearable
            w={200}
          />
          <Tooltip label="Edit view (columns, order, wrap, modules)">
            <ActionIcon
              variant="subtle"
              aria-label="Edit view"
              disabled={!isAvailable('view.edit')}
              onClick={() => run('view.edit')}
            >
              <IconPencil size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
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
            {showVerification && (
              <col
                ref={colRef(VERIFICATION_COLUMN)}
                style={{ width: columnWidth(VERIFICATION_COLUMN, DEFAULT_VERIFICATION_COLUMN_WIDTH) }}
              />
            )}
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
                onOpenMenu={(kind, position) => openColumnMenu(ID_COLUMN, 'Id', null, kind, position)}
              />
              {visibleAttributes.map((attr) => (
                <ResizableHeaderCell
                  key={attr.id}
                  columnKey={attr.id}
                  label={attr.name}
                  defaultWidth={defaultColumnWidth(attr)}
                  columns={columns}
                  order={columnOrder}
                  onOpenMenu={(kind, position) => openColumnMenu(attr.id, attr.name, attr, kind, position)}
                />
              ))}
              {showVerification && (
                // Computed, read-only: only the view (layout) menu, never attribute properties.
                <ResizableHeaderCell
                  columnKey={VERIFICATION_COLUMN}
                  label="Verification status"
                  defaultWidth={DEFAULT_VERIFICATION_COLUMN_WIDTH}
                  columns={columns}
                  onOpenMenu={(_kind, position) =>
                    openColumnMenu(VERIFICATION_COLUMN, 'Verification status', null, 'view', position)
                  }
                />
              )}
              <Table.Th>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody ref={tbodyRef}>
            {rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={visibleAttributes.length + (showVerification ? 3 : 2)}>
                  <Text c="dimmed" ta="center" py="md">
                    No requirements yet.
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
            {rows.map(({ requirement: req, depth, hasChildren }) => {
              const appearance = rowAppearance(req.attributes);
              const selected = selectedId === req.id;
              return (
                <Table.Tr
                  key={req.id}
                  ref={(el) => {
                    if (el) rowRefs.current.set(req.id, el);
                    else rowRefs.current.delete(req.id);
                  }}
                  data-requirement-row={req.id}
                  {...contextMenuTargetProps}
                  onClick={() => commands.selectRequirement(req.id)}
                  bg={selected ? 'var(--mantine-color-blue-light)' : undefined}
                  data-row-height={rowSizes.heightOf(req.id)}
                  data-row-bold={appearance.bold || undefined}
                  data-drop-target={dropTargetId === req.id || undefined}
                  onDragOver={(e) => {
                    if (locked || !hasRequirementDragData(e.dataTransfer)) return;
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'link';
                    if (dropTargetId !== req.id) setDropTargetId(req.id);
                  }}
                  onDragLeave={(e) => {
                    if (e.relatedTarget instanceof Node && e.currentTarget.contains(e.relatedTarget)) return;
                    setDropTargetId((current) => (current === req.id ? null : current));
                  }}
                  onDrop={(e) => {
                    if (locked || !hasRequirementDragData(e.dataTransfer)) return;
                    e.preventDefault();
                    setDropTargetId(null);
                    const payload = readRequirementDragData(e.dataTransfer);
                    if (payload) void dropLink(payload, req.id, moduleId);
                  }}
                  style={{
                    cursor: 'pointer',
                    ...rowSizes.rowStyle(req.id),
                    // The selection highlight wins over a rule's background.
                    ...(selected ? { color: appearance.style.color } : appearance.style),
                  }}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    commands.selectRequirement(req.id);
                    openMenu({ x: e.clientX, y: e.clientY, label: req.id, entries: REQUIREMENT_CONTEXT_MENU, targets: { requirement: req } });
                  }}
                >
                  <Table.Td
                    data-column-key={ID_COLUMN}
                    style={treeColumn === ID_COLUMN ? { paddingLeft: 12 + depth * 20 } : undefined}
                  >
                    {/* Left click on the Id: the navigation menu (the row is selected too). */}
                    <div
                      className="reqman-cell-clip"
                      data-fade={columns.isSingleLine(ID_COLUMN) ? undefined : ''}
                      title="Click for navigation, right-click for editing; drag onto a requirement of another module to link"
                      draggable
                      onDragStart={(e) =>
                        setRequirementDragData(e.dataTransfer, { projectId, moduleId, requirementIds: [req.id] })
                      }
                      onClick={(e) =>
                        openMenu({
                          x: e.clientX,
                          y: e.clientY,
                          label: req.id,
                          entries: REQUIREMENT_NAVIGATION_MENU,
                          targets: { requirement: req },
                        })
                      }
                    >
                      <Group gap={4} wrap="nowrap">
                        {treeColumn === ID_COLUMN && hasChildren && (
                          <ActionIcon
                            variant="subtle"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleExpanded(req.id);
                            }}
                          >
                            {collapsed.has(req.id) ? <IconChevronRight size={14} /> : <IconChevronDown size={14} />}
                          </ActionIcon>
                        )}
                        <Text fw={600} truncate={columns.isSingleLine(ID_COLUMN) ? 'end' : undefined} style={{ minWidth: 0 }}>
                          {req.id}
                        </Text>
                      </Group>
                    </div>
                    <RowResizeHandle rowId={req.id} rows={rowSizes} focusable />
                  </Table.Td>
                  {visibleAttributes.map((attr) => (
                    <Table.Td key={attr.id} data-column-key={attr.id}>
                      <div className="reqman-cell-clip" data-fade={columns.isSingleLine(attr.id) ? undefined : ''}>
                        <Group gap={4} wrap="nowrap" pl={attr.id === treeColumn ? depth * 20 : 0}>
                          {attr.id === treeColumn && hasChildren && (
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
                              disabled={
                                updateRequirement.isPending || (attr.id === statusAttrId ? !statusEditable : locked)
                              }
                              projectId={projectId}
                              onNavigateToLinked={handleNavigateToLinked}
                              onLinkMenu={(link, kind, position) => openLinkMenu(req, attr.id, link, kind, position)}
                              wrap={!columns.isSingleLine(attr.id)}
                            />
                          </div>
                        </Group>
                      </div>
                      <RowResizeHandle rowId={req.id} rows={rowSizes} />
                    </Table.Td>
                  ))}
                  {showVerification && (
                    <Table.Td
                      data-column-key={VERIFICATION_COLUMN}
                      title="Computed from the linked test cases and their latest executions (read-only)"
                    >
                      <div className="reqman-cell-clip">
                        {statuses?.[req.id] ? (
                          <StatusBadge status={statuses[req.id]} />
                        ) : (
                          <Text size="sm" c="dimmed">
                            {verificationQuery.isLoading ? '…' : verificationQuery.isError ? '?' : '–'}
                          </Text>
                        )}
                      </div>
                      <RowResizeHandle rowId={req.id} rows={rowSizes} />
                    </Table.Td>
                  )}
                  <Table.Td>
                    <div className="reqman-cell-clip">
                      <Group gap={4} wrap="nowrap">
                        <Tooltip label="Add child">
                          <ActionIcon
                            variant="subtle"
                            disabled={locked}
                            onClick={() => run('requirement.addChild', { requirement: req })}
                          >
                            <IconPlus size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Move to parent">
                          <ActionIcon
                            variant="subtle"
                            disabled={locked}
                            onClick={() => run('requirement.moveTo', { requirement: req })}
                          >
                            <IconArrowsMove size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Link to">
                          <ActionIcon
                            variant="subtle"
                            disabled={locked}
                            onClick={() => run('requirement.link', { requirement: req })}
                          >
                            <IconLink size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Remove">
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            disabled={locked}
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
              );
            })}
          </Table.Tbody>
        </Table>
      </div>

      {/* Collapsible and resizable; the table takes the width it frees. */}
      <RightSidePanel>
        <RequirementDetailPanel
          projectId={projectId}
          moduleId={moduleId}
          attributes={attributes}
          requirement={selectedRequirement}
          onSave={handleCellSave}
          saving={updateRequirement.isPending || locked}
          onNavigateToLinked={handleNavigateToLinked}
          onClose={() => run('view.toggleDetailPanel')}
          verification={isRequirementsModule && selectedRequirement ? verificationOf(selectedRequirement.id) : undefined}
        />
      </RightSidePanel>
      </Group>

    </Stack>
  );
}

