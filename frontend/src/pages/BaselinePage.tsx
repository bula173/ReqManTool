import { Alert, Anchor, Badge, Card, Center, Group, Loader, Select, Stack, Table, Text, Title } from '@mantine/core';
import { IconInfoCircle, IconLock } from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useBaseline, useBaselineRequirements } from '../api/queries';
import type { Requirement } from '../api/types';

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
  const attributes = snapshot.project.attributes;
  const rows = treeOrder(requirementsQuery.data ?? []);

  return (
    <Stack gap="md">
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

      <Card withBorder>
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
          <Table.ScrollContainer minWidth={600}>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Id</Table.Th>
                  {attributes.map((attr) => (
                    <Table.Th key={attr.id}>{attr.name}</Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map(({ requirement, depth }) => (
                  <Table.Tr key={requirement.id}>
                    <Table.Td style={{ whiteSpace: 'nowrap', paddingLeft: 12 + depth * 20 }}>
                      {requirement.id}
                    </Table.Td>
                    {attributes.map((attr) => (
                      <Table.Td key={attr.id}>
                        <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                          {formatValue(requirement.attributes[attr.id])}
                        </Text>
                      </Table.Td>
                    ))}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
    </Stack>
  );
}
