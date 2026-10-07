import { Alert, Anchor, Badge, Card, Center, Group, Loader, Menu, Select, Stack, Table, Text, Title } from '@mantine/core';
import { IconInfoCircle, IconLock } from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { applicableAttributes } from '../api/applicability';
import { ApiError } from '../api/client';
import { useBaseline, useBaselineRequirements } from '../api/queries';
import type { Requirement } from '../api/types';
import { ResizableHeaderCell } from '../components/ResizableHeaderCell';
import { RowResizeHandle } from '../components/RowResizeHandle';
import { useRowAppearance } from '../components/rowStyles';
import {
  DEFAULT_ID_COLUMN_WIDTH,
  ID_COLUMN,
  defaultColumnWidth,
  moduleTableKey,
  useResizableColumns,
  useResizableRows,
} from '../hooks/columnLayout';

/** Depth-first tree order: siblings by `order`, then id (same as the exports). */
function treeOrder(requirements: Requirement[]): { requirement: Requirement; depth: number }[] {
  const byParent = new Map<string | null, Requirement[]>();
  const ids = new Set(requirements.map((r) => r.id));
  for (const requirement of requirements) {
    // A parent outside the baseline's module data is shown at top level.
    const parent = requirement.parent_id !== null && ids.has(requirement.parent_id) ? requirement.parent_id : null;
    const siblings = byParent.get(parent) ?? [];
    siblings.push(requirement);
    byParent.set(parent, siblings);
  }
  const rows: { requirement: Requirement; depth: number }[] = [];
  const walk = (parent: string | null, depth: number) => {
    const children = (byParent.get(parent) ?? []).sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
    for (const child of children) {
      rows.push({ requirement: child, depth });
      walk(child.id, depth + 1);
    }
  };
  walk(null, 0);
  return rows;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/**
 * Read-only view of a project at a baseline (`baseline/<name>` tag). There is
 * deliberately no editing, no context menu and no shortcut here: nothing on
 * this page can change data (the backend accepts no mutation on a baseline
 * either).
 */
export function BaselinePage() {
  const { projectId, baselineName } = useParams<{ projectId: string; baselineName: string }>();
  const baselineQuery = useBaseline(projectId, baselineName);
  const [selectedModule, setSelectedModule] = useState<string | null>(null);

  const snapshot = baselineQuery.data;
  const moduleId = selectedModule ?? snapshot?.modules[0]?.id ?? null;
  const requirementsQuery = useBaselineRequirements(projectId, baselineName, moduleId ?? undefined);
  // The module's own "All attributes" layout: a baseline looks like the module's table.
  const tableKey = moduleTableKey(projectId ?? '', moduleId ?? '', null);
  const columns = useResizableColumns(tableKey);
  const { tableRef, colRef } = columns;
  const rowSizes = useResizableRows(tableKey);
  const { tbodyRef } = rowSizes;
  // The baseline's own row styling rules (none if its project.json predates them).
  const rowAppearance = useRowAppearance(snapshot?.project.row_styles, snapshot?.project.attributes);

  if (!projectId || !baselineName) return null;
  if (baselineQuery.isLoading) {
    return (
      <Center h="60vh">
        <Loader />
      </Center>
    );
  }
  if (baselineQuery.isError || !snapshot) {
    return (
      <Alert icon={<IconInfoCircle size={16} />} color="red" title={`Failed to load baseline "${baselineName}"`}>
        {baselineQuery.error instanceof ApiError || baselineQuery.error instanceof Error
          ? baselineQuery.error.message
          : 'Unknown error'}
      </Alert>
    );
  }

  const module = snapshot.modules.find((m) => m.id === moduleId);
  const attributes = applicableAttributes(snapshot.project.attributes, moduleId);
  const rows = treeOrder(requirementsQuery.data ?? []);
  const tableWidth =
    columns.widthOf(ID_COLUMN, DEFAULT_ID_COLUMN_WIDTH) +
    attributes.reduce((sum, attr) => sum + columns.widthOf(attr.id, defaultColumnWidth(attr)), 0);
  const columnMenu = (
    <>
      <Menu.Item disabled={!columns.hasWidthOverrides} onClick={columns.resetWidths}>
        Reset column widths
      </Menu.Item>
      <Menu.Item disabled={!rowSizes.hasHeightOverrides} onClick={rowSizes.resetHeights}>
        Reset row heights
      </Menu.Item>
    </>
  );

  // Fills the page area: the card shrinks to fit it and the table scrolls inside, under a sticky header row.
  return (
    <Stack gap="md" className="reqman-fill-page">
      <div>
        <Group gap="xs">
          <Title order={2}>
            Baseline {snapshot.name}{' '}
            <Text span c="dimmed" fw={400}>
              (read-only)
            </Text>
          </Title>
          <Badge color="gray" variant="light" leftSection={<IconLock size={12} />}>
            read-only
          </Badge>
        </Group>
        <Text size="sm" c="dimmed">
          Project "{snapshot.project.name}" as it was at baseline {snapshot.name} (commit{' '}
          <Text span ff="monospace">
            {snapshot.commit.slice(0, 8)}
          </Text>
          ). Nothing here can be edited.{' '}
          <Anchor component={Link} to={`/projects/${projectId}`} size="sm">
            Back to the project
          </Anchor>
        </Text>
      </div>

      <Card withBorder className="reqman-card-table" style={{ flex: '0 1 auto', minHeight: 0 }}>
        <Group justify="space-between" mb="sm">
          <Title order={4}>{module ? `${module.name} (${module.prefix})` : 'Requirements'}</Title>
          {snapshot.modules.length > 0 && (
            <Select
              size="xs"
              w={260}
              label={undefined}
              aria-label="Module"
              data={snapshot.modules.map((m) => ({ value: m.id, label: m.name }))}
              value={moduleId}
              onChange={setSelectedModule}
              allowDeselect={false}
            />
          )}
        </Group>
        {snapshot.modules.length === 0 && (
          <Text size="sm" c="dimmed">
            This baseline has no modules.
          </Text>
        )}
        {requirementsQuery.isLoading && <Loader size="sm" />}
        {requirementsQuery.isError && (
          <Text size="sm" c="red">
            Failed to load the requirements:{' '}
            {requirementsQuery.error instanceof ApiError || requirementsQuery.error instanceof Error
              ? requirementsQuery.error.message
              : 'Unknown error'}
          </Text>
        )}
        {module && requirementsQuery.data && rows.length === 0 && (
          <Text size="sm" c="dimmed">
            This module had no requirements at the baseline.
          </Text>
        )}
        {rows.length > 0 && (
          <div className="reqman-table-scroll" style={{ flex: '0 1 auto' }}>
            <Table
              ref={tableRef}
              className="reqman-resizable-table"
              striped
              highlightOnHover
              stickyHeader
              style={{ width: tableWidth, minWidth: tableWidth }}
            >
              <colgroup>
                <col
                  ref={colRef(ID_COLUMN)}
                  style={{ width: columns.widthOf(ID_COLUMN, DEFAULT_ID_COLUMN_WIDTH) }}
                />
                {attributes.map((attr) => (
                  <col
                    key={attr.id}
                    ref={colRef(attr.id)}
                    style={{ width: columns.widthOf(attr.id, defaultColumnWidth(attr)) }}
                  />
                ))}
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
                  {attributes.map((attr) => (
                    <ResizableHeaderCell
                      key={attr.id}
                      columnKey={attr.id}
                      label={attr.name}
                      defaultWidth={defaultColumnWidth(attr)}
                      columns={columns}
                      menuExtra={columnMenu}
                    />
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody ref={tbodyRef}>
                {rows.map(({ requirement, depth }) => {
                  const appearance = rowAppearance(requirement.attributes);
                  return (
                    <Table.Tr
                      key={requirement.id}
                      data-row-height={rowSizes.heightOf(requirement.id)}
                      data-row-bold={appearance.bold || undefined}
                      style={{ ...rowSizes.rowStyle(requirement.id), ...appearance.style }}
                    >
                      <Table.Td data-column-key={ID_COLUMN} style={{ paddingLeft: 12 + depth * 20 }}>
                        <div
                          className="reqman-cell-clip"
                          data-fade={columns.isSingleLine(ID_COLUMN) ? undefined : ''}
                          style={{
                            overflow: 'hidden',
                            whiteSpace: columns.isSingleLine(ID_COLUMN) ? 'nowrap' : undefined,
                            textOverflow: 'ellipsis',
                          }}
                        >
                          {requirement.id}
                        </div>
                        <RowResizeHandle rowId={requirement.id} rows={rowSizes} focusable />
                      </Table.Td>
                      {attributes.map((attr) => {
                        const text = formatValue(requirement.attributes[attr.id]);
                        return (
                          <Table.Td key={attr.id} data-column-key={attr.id}>
                            <div className="reqman-cell-clip" data-fade={columns.isSingleLine(attr.id) ? undefined : ''}>
                              {columns.isSingleLine(attr.id) ? (
                                <Text size="sm" truncate="end" title={text}>
                                  {text}
                                </Text>
                              ) : (
                                <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                                  {text}
                                </Text>
                              )}
                            </div>
                            <RowResizeHandle rowId={requirement.id} rows={rowSizes} />
                          </Table.Td>
                        );
                      })}
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </div>
        )}
      </Card>
    </Stack>
  );
}
