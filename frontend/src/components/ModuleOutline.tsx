import { ActionIcon, Badge, Center, CloseButton, Group, Loader, ScrollArea, Stack, Text, TextInput, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconBook,
  IconBulb,
  IconCheckbox,
  IconChevronDown,
  IconChevronRight,
  IconChevronsDown,
  IconChevronsUp,
  IconFilter,
  IconHeading,
  IconInfoCircle,
  IconTag,
  IconTestPipe,
} from '@tabler/icons-react';
import { useEffect, useRef, useState, type ComponentType, type DragEvent, type KeyboardEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAppActions } from '../actions/context';
import { REQUIREMENT_CONTEXT_MENU } from '../actions/registry';
import { contextMenuTargetProps } from '../actions/shortcuts';
import { ApiError } from '../api/client';
import { useModule, useMoveRequirement, useProject, useRequirements } from '../api/queries';
import {
  ancestorIdsOf,
  buildOutlineRows,
  findObjectTypeAttribute,
  objectTextSnippet,
  parentIds,
  planDrop,
  type DropPlan,
  type DropPosition,
  type OutlineRow,
} from './requirementOutline';
import { useRowAppearance } from './rowStyles';

/** Icons of the Object Type values the built-in templates use; any other value gets a tag. */
const OBJECT_TYPE_ICONS: Record<string, ComponentType<{ size?: number | string }>> = {
  heading: IconHeading,
  requirement: IconCheckbox,
  information: IconInfoCircle,
  definition: IconBook,
  assumption: IconBulb,
  'test case': IconTestPipe,
};

const INDENT = 14;

interface DropTarget {
  /** The node under the pointer; null = the end of the module root. */
  id: string | null;
  position: DropPosition;
}

function ObjectTypeIcon({ value }: { value: unknown }) {
  if (typeof value !== 'string' || value.trim() === '') return null;
  const Icon = OBJECT_TYPE_ICONS[value.trim().toLowerCase()] ?? IconTag;
  return (
    <span title={value} aria-label={`Object Type: ${value}`} style={{ display: 'inline-flex', flex: 'none', color: 'var(--mantine-color-dimmed)' }}>
      <Icon size={14} />
    </span>
  );
}

function dropShadow(target: DropTarget | null, id: string): string | undefined {
  if (!target || target.id !== id) return undefined;
  if (target.position === 'before') return 'inset 0 2px 0 var(--mantine-color-blue-filled)';
  // Below the row: after it, or before the first child that follows an expanded row.
  if (target.position === 'after' || target.position === 'first-child') {
    return 'inset 0 -2px 0 var(--mantine-color-blue-filled)';
  }
  return 'inset 0 0 0 1px var(--mantine-color-blue-filled)';
}

/**
 * The Module tab of the left panel: a collapsible outline of the open
 * module's requirement tree. Clicking a node selects it and reveals it in the
 * table and the detail panel (through the module page's `?highlight=`);
 * right-click opens the requirement context menu of the action registry.
 * Drag-and-drop reorders and reparents within the module with one
 * positional Move call per drop: the server owns every rule. Mount it
 * with `key={projectId/moduleId}` so its state starts fresh per module.
 */
export function ModuleOutline({ projectId, moduleId }: { projectId: string; moduleId: string }) {
  const projectQuery = useProject(projectId);
  const moduleQuery = useModule(projectId, moduleId);
  const requirementsQuery = useRequirements(projectId, moduleId);
  const moveRequirement = useMoveRequirement(projectId, moduleId);
  const { state, openMenu } = useAppActions();
  const [, setSearchParams] = useSearchParams();

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const nodeRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const pendingScrollRef = useRef<string | null>(null);
  // Row styling rules (font/background color, bold) by enum attribute value.
  const rowAppearance = useRowAppearance(projectQuery.data?.row_styles, projectQuery.data?.attributes);

  const requirements = requirementsQuery.data;
  // The selection belongs to the open module (AppActionsProvider scopes it).
  const selectedId = state.moduleId === moduleId ? (state.selectedRequirement?.id ?? null) : null;

  // A requirement selected anywhere (table, outline, linked navigation) is
  // revealed here too: its collapsed ancestors open and it scrolls into view.
  useEffect(() => {
    if (!selectedId || !requirements) return;
    const ancestors = ancestorIdsOf(requirements, selectedId);
    setCollapsed((prev) => {
      if (!ancestors.some((id) => prev.has(id))) return prev;
      const next = new Set(prev);
      for (const id of ancestors) next.delete(id);
      return next;
    });
    pendingScrollRef.current = selectedId;
    // Only on a new selection: a refetch must not undo the user's collapsing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  useEffect(() => {
    const id = pendingScrollRef.current;
    if (!id) return;
    const node = nodeRefs.current.get(id);
    if (!node) return;
    node.scrollIntoView({ block: 'nearest' });
    pendingScrollRef.current = null;
  });

  if (projectQuery.isLoading || moduleQuery.isLoading || requirementsQuery.isLoading) {
    return (
      <Center py="md">
        <Loader size="sm" />
      </Center>
    );
  }
  const error = projectQuery.error ?? moduleQuery.error ?? requirementsQuery.error;
  if (error || !requirements) {
    return (
      <Text c="red" size="sm" px="sm" py="xs">
        Failed to load the module{error instanceof Error ? `: ${error.message}` : ''}
      </Text>
    );
  }

  const objectTypeAttribute = findObjectTypeAttribute(projectQuery.data?.attributes ?? []);
  const filtering = filter.trim() !== '';
  const rows = buildOutlineRows(requirements, collapsed, filter);
  const expandable = parentIds(requirements);
  const canDrag = !filtering && !busy;
  const focusableId = rows.some((r) => r.requirement.id === selectedId) ? selectedId : (rows[0]?.requirement.id ?? null);

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const reveal = (id: string) => {
    state.commands.selectRequirement(id);
    // The module page opens the row's ancestors, selects it and scrolls to it.
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set('highlight', id);
        return next;
      },
      { replace: true },
    );
  };

  const applyDrop = async (draggedId: string, plan: DropPlan) => {
    setBusy(true);
    try {
      await moveRequirement.mutateAsync({ id: draggedId, parentId: plan.parentId, index: plan.index });
      if (plan.parentId) {
        const parentId = plan.parentId;
        setCollapsed((prev) => {
          if (!prev.has(parentId)) return prev;
          const next = new Set(prev);
          next.delete(parentId);
          return next;
        });
      }
      notifications.show({ message: `Moved ${draggedId}`, color: 'green' });
    } catch (err) {
      notifications.show({ message: err instanceof ApiError ? err.message : 'Failed to move requirement', color: 'red' });
    } finally {
      setBusy(false);
    }
  };

  const endDrag = () => {
    setDragId(null);
    setDropTarget(null);
  };

  /** Accepts the drag over a node (or the root zone) when the drop would change something. */
  const handleDragOver = (event: DragEvent<HTMLDivElement>, target: DropTarget) => {
    if (!dragId) return; // not one of this outline's nodes
    const plan = planDrop(requirements, dragId, target.id, target.position);
    if (!plan) {
      if (dropTarget !== null) setDropTarget(null);
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    if (dropTarget?.id !== target.id || dropTarget.position !== target.position) setDropTarget(target);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const draggedId = dragId;
    const target = dropTarget;
    endDrag();
    if (!draggedId || !target) return;
    const plan = planDrop(requirements, draggedId, target.id, target.position);
    if (plan) void applyDrop(draggedId, plan);
  };

  /** Top quarter: before; bottom quarter: after (first child for an expanded node, whose children follow); else inside. */
  const positionAt = (event: DragEvent<HTMLDivElement>, row: OutlineRow): DropPosition => {
    const rect = event.currentTarget.getBoundingClientRect();
    const y = rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0.5;
    if (y < 0.25) return 'before';
    if (y > 0.75) return row.expanded ? 'first-child' : 'after';
    return 'inside';
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>, row: OutlineRow) => {
    const id = row.requirement.id;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      reveal(id);
    } else if (event.key === 'ArrowRight' && row.hasChildren && !row.expanded && !filtering) {
      event.preventDefault();
      toggle(id);
    } else if (event.key === 'ArrowLeft' && row.expanded && !filtering) {
      event.preventDefault();
      toggle(id);
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return; // Alt+↑/↓ are Move Up/Down
      event.preventDefault();
      const index = rows.findIndex((r) => r.requirement.id === id);
      const next = rows[index + (event.key === 'ArrowDown' ? 1 : -1)];
      if (next) nodeRefs.current.get(next.requirement.id)?.focus();
    }
  };

  return (
    <Stack gap={6} h="100%">
      <Stack gap={6} px="xs" pt="xs">
        <Group gap={6} wrap="nowrap">
          <Text fw={600} size="sm" truncate="end" style={{ flex: 1, minWidth: 0 }}>
            {moduleQuery.data?.name ?? moduleId}
          </Text>
          {moduleQuery.data && (
            <Badge variant="outline" size="sm" style={{ flex: 'none' }}>
              {moduleQuery.data.prefix}
            </Badge>
          )}
        </Group>
        <Group gap={4} wrap="nowrap">
          <TextInput
            size="xs"
            placeholder="Filter by id or text"
            aria-label="Filter the outline"
            leftSection={<IconFilter size={14} />}
            rightSection={
              filtering ? <CloseButton size="xs" aria-label="Clear filter" onClick={() => setFilter('')} /> : undefined
            }
            value={filter}
            onChange={(event) => setFilter(event.currentTarget.value)}
            style={{ flex: 1 }}
          />
          <Tooltip label="Expand all">
            <ActionIcon
              variant="default"
              size="md"
              aria-label="Expand all"
              disabled={filtering || collapsed.size === 0}
              onClick={() => setCollapsed(new Set())}
            >
              <IconChevronsDown size={14} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Collapse all">
            <ActionIcon
              variant="default"
              size="md"
              aria-label="Collapse all"
              disabled={filtering || expandable.size === 0}
              onClick={() => setCollapsed(expandable)}
            >
              <IconChevronsUp size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
        {busy && (
          <Group gap={6}>
            <Loader size="xs" />
            <Text size="xs" c="dimmed">
              Moving…
            </Text>
          </Group>
        )}
      </Stack>
      <ScrollArea flex={1} type="auto">
        {requirements.length === 0 && (
          <Text c="dimmed" size="sm" px="sm">
            No requirements yet
          </Text>
        )}
        {requirements.length > 0 && rows.length === 0 && (
          <Text c="dimmed" size="sm" px="sm">
            No requirements match the filter
          </Text>
        )}
        <div
          role="tree"
          aria-label="Requirement outline"
          style={{ padding: '0 var(--mantine-spacing-xs) var(--mantine-spacing-xs)' }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropTarget(null);
          }}
        >
          {rows.map((row) => {
            const { requirement: req, depth, hasChildren, expanded } = row;
            const selected = req.id === selectedId;
            const insideDrop = dropTarget?.id === req.id && dropTarget.position === 'inside';
            const appearance = rowAppearance(req.attributes);
            return (
              <div
                key={req.id}
                ref={(el) => {
                  if (el) nodeRefs.current.set(req.id, el);
                  else nodeRefs.current.delete(req.id);
                }}
                role="treeitem"
                aria-level={depth + 1}
                aria-expanded={hasChildren ? expanded : undefined}
                aria-selected={selected}
                tabIndex={req.id === focusableId ? 0 : -1}
                draggable={canDrag}
                onClick={() => reveal(req.id)}
                onKeyDown={(event) => handleKeyDown(event, row)}
                {...contextMenuTargetProps}
                onContextMenu={(event) => {
                  event.preventDefault();
                  state.commands.selectRequirement(req.id);
                  openMenu({
                    x: event.clientX,
                    y: event.clientY,
                    label: req.id,
                    entries: REQUIREMENT_CONTEXT_MENU,
                    targets: { requirement: req },
                  });
                }}
                onDragStart={(event) => {
                  event.dataTransfer.setData('application/x-reqman-requirement', req.id);
                  event.dataTransfer.effectAllowed = 'move';
                  setDragId(req.id);
                }}
                onDragEnd={endDrag}
                onDragOver={(event) => handleDragOver(event, { id: req.id, position: positionAt(event, row) })}
                onDrop={handleDrop}
                data-row-bold={appearance.bold || undefined}
                style={{
                  ...appearance.style,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  minHeight: 26,
                  paddingLeft: 2 + depth * INDENT,
                  paddingRight: 4,
                  borderRadius: 'var(--mantine-radius-sm)',
                  cursor: 'pointer',
                  userSelect: 'none',
                  background: insideDrop
                    ? 'var(--mantine-color-blue-light-hover)'
                    : selected
                      ? 'var(--mantine-color-blue-light)'
                      : appearance.style.backgroundColor,
                  boxShadow: dropShadow(dropTarget, req.id),
                  opacity: dragId === req.id ? 0.5 : 1,
                }}
              >
                {hasChildren ? (
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    size="xs"
                    tabIndex={-1}
                    aria-label={expanded ? `Collapse ${req.id}` : `Expand ${req.id}`}
                    disabled={filtering}
                    onClick={(event) => {
                      event.stopPropagation();
                      toggle(req.id);
                    }}
                  >
                    {expanded ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
                  </ActionIcon>
                ) : (
                  <span style={{ width: 18, flex: 'none' }} />
                )}
                {objectTypeAttribute && <ObjectTypeIcon value={req.attributes[objectTypeAttribute.id]} />}
                <Text size="xs" fw={600} style={{ flex: 'none' }}>
                  {req.id}
                </Text>
                <Text
                  size="xs"
                  c={appearance.style.color ? undefined : 'dimmed'}
                  truncate="end"
                  style={{ flex: 1, minWidth: 0 }}
                >
                  {objectTextSnippet(req)}
                </Text>
              </div>
            );
          })}
          {dragId && (
            <div
              onDragOver={(event) => handleDragOver(event, { id: null, position: 'inside' })}
              onDrop={handleDrop}
              style={{
                marginTop: 4,
                padding: '6px 8px',
                border: '1px dashed var(--mantine-color-default-border)',
                borderRadius: 'var(--mantine-radius-sm)',
                background: dropTarget?.id === null ? 'var(--mantine-color-blue-light-hover)' : undefined,
              }}
            >
              <Text size="xs" c="dimmed">
                Drop here to move to the end of the top level
              </Text>
            </div>
          )}
        </div>
      </ScrollArea>

    </Stack>
  );
}
