import { Badge, Card, Group, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import type { BaselineModuleDiff, CompareChanges, HistoryDiff } from '../api/types';
import { richTextToPlain } from './richText';
import { useRowStyleCss } from './rowStyles';

/**
 * The requirement-level differences of the baseline compare engine: a
 * summary, attribute definition changes, per-module requirement changes with
 * per-attribute diffs, and link changes. Shown by the baseline compare page
 * and by a commit's details (a commit against its first parent). Read-only.
 */

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '(empty)';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  // Rich text is shown as its plain projection.
  return typeof value === 'string' ? richTextToPlain(value).replace(/\s*\n\s*/g, ' ') : JSON.stringify(value);
}

function diffLabel(diff: HistoryDiff, attributeName: (id: string) => string): string {
  if (diff.kind === 'attribute') {
    return `${attributeName(diff.attribute_id ?? '')}: ${formatValue(diff.old)} → ${formatValue(diff.new)}`;
  }
  if (diff.kind === 'parent') {
    return diff.new ? `moved under ${String(diff.new)}` : 'moved to top level';
  }
  return `reordered (${formatValue(diff.old)} → ${formatValue(diff.new)})`;
}

function ModuleDiffCard({
  module,
  attributeName,
}: {
  module: BaselineModuleDiff;
  attributeName: (id: string) => string;
}) {
  // Requirement ids take their row style (computed by the server per side).
  const styleCss = useRowStyleCss();
  const idText = (id: string) => {
    const style = module.row_styles?.[id] ?? null;
    return (
      <Text span inherit fw={style?.bold ? 700 : undefined} style={styleCss(style)}>
        {id}
      </Text>
    );
  };
  return (
    <Card withBorder>
      <Group gap="xs" mb="xs">
        <Title order={5}>{module.name}</Title>
        {module.change && (
          <Badge color={module.change === 'added' ? 'green' : 'red'} variant="light">
            module {module.change}
          </Badge>
        )}
      </Group>
      <Stack gap={4}>
        {module.added.map((id) => (
          <Text size="sm" key={`a-${id}`}>
            <Badge size="xs" color="green" variant="light" mr={6}>
              added
            </Badge>
            {idText(id)}
          </Text>
        ))}
        {module.removed.map((id) => (
          <Text size="sm" key={`r-${id}`}>
            <Badge size="xs" color="red" variant="light" mr={6}>
              removed
            </Badge>
            {idText(id)}
          </Text>
        ))}
        {module.modified.map((entry) => (
          <div key={`m-${entry.id}`}>
            <Text size="sm">
              <Badge size="xs" color="yellow" variant="light" mr={6}>
                modified
              </Badge>
              {idText(entry.id)}
            </Text>
            <Stack gap={0} pl="xl">
              {entry.diffs.map((diff, index) => (
                <Text size="xs" c="dimmed" key={index}>
                  {diffLabel(diff, attributeName)}
                </Text>
              ))}
            </Stack>
          </div>
        ))}
      </Stack>
    </Card>
  );
}

export function CompareResultView({
  result,
  title,
  attributeName,
  noChangesText = 'No differences.',
  extraUnchanged = true,
}: {
  result: CompareChanges;
  title: ReactNode;
  attributeName: (id: string) => string;
  noChangesText?: string;
  /** Whether everything outside `result` is unchanged too (a commit's notes may still list changes). */
  extraUnchanged?: boolean;
}) {
  const summaryItems: [string, number][] = [
    ['Requirements added', result.summary.requirements_added],
    ['Requirements removed', result.summary.requirements_removed],
    ['Requirements modified', result.summary.requirements_modified],
    ['Links added', result.summary.links_added],
    ['Links removed', result.summary.links_removed],
    ['Modules added', result.summary.modules_added],
    ['Modules removed', result.summary.modules_removed],
    [
      'Attribute definitions changed',
      result.summary.attributes_added + result.summary.attributes_removed + result.summary.attributes_changed,
    ],
  ];
  const unchanged =
    result.modules.length === 0 &&
    result.links.added.length === 0 &&
    result.links.removed.length === 0 &&
    summaryItems.every(([, count]) => count === 0);

  return (
    <>
      <Card withBorder>
        <Title order={4} mb="xs">
          {title}
        </Title>
        {unchanged ? (
          <Text size="sm" c="dimmed">
            {extraUnchanged ? noChangesText : 'No requirement, link or attribute definition changes.'}
          </Text>
        ) : (
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            {summaryItems.map(([label, count]) => (
              <div key={label}>
                <Text size="xl" fw={600}>
                  {count}
                </Text>
                <Text size="xs" c="dimmed">
                  {label}
                </Text>
              </div>
            ))}
          </SimpleGrid>
        )}
      </Card>

      {(result.attributes.added.length > 0 ||
        result.attributes.removed.length > 0 ||
        result.attributes.changed.length > 0) && (
        <Card withBorder>
          <Title order={5} mb="xs">
            Attribute definitions
          </Title>
          <Stack gap={4}>
            {result.attributes.added.map((attr) => (
              <Text size="sm" key={`a-${attr.id}`}>
                <Badge size="xs" color="green" variant="light" mr={6}>
                  added
                </Badge>
                {attr.name}
              </Text>
            ))}
            {result.attributes.removed.map((attr) => (
              <Text size="sm" key={`r-${attr.id}`}>
                <Badge size="xs" color="red" variant="light" mr={6}>
                  removed
                </Badge>
                {attr.name}
              </Text>
            ))}
            {result.attributes.changed.map((attr) => (
              <Text size="sm" key={`c-${attr.id}`}>
                <Badge size="xs" color="yellow" variant="light" mr={6}>
                  changed
                </Badge>
                {attr.name}
              </Text>
            ))}
          </Stack>
        </Card>
      )}

      {result.modules.map((module) => (
        <ModuleDiffCard key={module.module_id} module={module} attributeName={attributeName} />
      ))}

      {(result.links.added.length > 0 || result.links.removed.length > 0) && (
        <Card withBorder>
          <Title order={5} mb="xs">
            Links
          </Title>
          <Table striped>
            <Table.Tbody>
              {result.links.added.map((link, index) => (
                <Table.Tr key={`a-${index}`}>
                  <Table.Td w={90}>
                    <Badge size="xs" color="green" variant="light">
                      added
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    {link.source_id} → {link.target_id}
                  </Table.Td>
                  <Table.Td c="dimmed">{link.name}</Table.Td>
                </Table.Tr>
              ))}
              {result.links.removed.map((link, index) => (
                <Table.Tr key={`r-${index}`}>
                  <Table.Td w={90}>
                    <Badge size="xs" color="red" variant="light">
                      removed
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    {link.source_id} → {link.target_id}
                  </Table.Td>
                  <Table.Td c="dimmed">{link.name}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}
    </>
  );
}
