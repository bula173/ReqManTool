import {
  Alert,
  Anchor,
  Badge,
  Card,
  Group,
  Loader,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconArrowRight, IconInfoCircle } from '@tabler/icons-react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useBaselineCompare, useBaselines, useProject } from '../api/queries';
import type { BaselineModuleDiff, HistoryDiff } from '../api/types';
import { richTextToPlain } from '../components/richText';
import { useRowStyleCss } from '../components/rowStyles';

const CURRENT = 'current';

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

/**
 * Compares two baselines, or a baseline with `current` (the working files on
 * disk, including uncommitted changes): a summary plus the per-module
 * requirement changes with per-attribute diffs, link changes and attribute
 * definition changes. Read-only.
 */
export function BaselineComparePage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [params, setParams] = useSearchParams();
  const from = params.get('from') ?? '';
  const to = params.get('to') ?? '';
  const baselinesQuery = useBaselines(projectId);
  const projectQuery = useProject(projectId);
  const compareQuery = useBaselineCompare(projectId, from || undefined, to || undefined);

  if (!projectId) return null;

  const names = (baselinesQuery.data?.baselines ?? []).map((b) => b.name);
  const sideOptions = (extra: string[]) => [
    ...extra.map((name) => ({ value: name, label: name === CURRENT ? 'Current state (working files)' : name })),
    ...names.map((name) => ({ value: name, label: `Baseline ${name}` })),
  ];
  const setSide = (key: 'from' | 'to') => (value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const result = compareQuery.data;
  // Attribute names: the live project's definitions, plus any named in the
  // compare payload itself (added/removed/changed definitions); unknown ids
  // (e.g. an attribute that existed only at the baseline) fall back to the id.
  const attributeNames = new Map<string, string>(
    (projectQuery.data?.attributes ?? []).map((attr) => [attr.id, attr.name]),
  );
  if (result) {
    for (const group of [result.attributes.added, result.attributes.removed, result.attributes.changed]) {
      for (const attr of group) attributeNames.set(attr.id, attr.name);
    }
  }
  const resolveAttribute = (id: string) => attributeNames.get(id) ?? id;

  const summaryItems = result
    ? [
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
      ]
    : [];
  const unchanged =
    result !== undefined && result.modules.length === 0 && result.links.added.length === 0 &&
    result.links.removed.length === 0 && summaryItems.every(([, count]) => count === 0);

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>Compare baselines</Title>
        <Text size="sm" c="dimmed">
          Requirement-level differences between two baselines, or a baseline and the current state (including
          uncommitted changes).{' '}
          <Anchor component={Link} to={`/projects/${projectId}`} size="sm">
            Back to the project
          </Anchor>
        </Text>
      </div>

      <Card withBorder>
        <Group align="flex-end" gap="sm">
          <Select
            label="From"
            placeholder="Pick a baseline"
            data={sideOptions([CURRENT])}
            value={from || null}
            onChange={setSide('from')}
            searchable
            w={260}
          />
          <IconArrowRight size={18} style={{ marginBottom: 8 }} />
          <Select
            label="To"
            placeholder="Pick a baseline or the current state"
            data={sideOptions([CURRENT])}
            value={to || null}
            onChange={setSide('to')}
            searchable
            w={260}
          />
        </Group>
        {baselinesQuery.data && !baselinesQuery.data.available && (
          <Alert color="orange" mt="sm" p="xs">
            <Text size="sm">{baselinesQuery.data.reason ?? 'This project has no readable repository.'}</Text>
          </Alert>
        )}
        {baselinesQuery.data?.available && names.length === 0 && (
          <Text size="sm" c="dimmed" mt="sm">
            This project has no baselines yet; create one from the project page's Git panel.
          </Text>
        )}
      </Card>

      {(!from || !to) && (
        <Text size="sm" c="dimmed">
          Pick both sides to compare.
        </Text>
      )}
      {compareQuery.isLoading && <Loader size="sm" />}
      {compareQuery.isError && (
        <Alert icon={<IconInfoCircle size={16} />} color="red" title="Failed to compare">
          {compareQuery.error instanceof ApiError || compareQuery.error instanceof Error
            ? compareQuery.error.message
            : 'Unknown error'}
        </Alert>
      )}

      {result && (
        <>
          <Card withBorder>
            <Title order={4} mb="xs">
              {result.from.name === CURRENT ? 'Current state' : `Baseline ${result.from.name}`} →{' '}
              {result.to.name === CURRENT ? 'Current state' : `Baseline ${result.to.name}`}
            </Title>
            {unchanged ? (
              <Text size="sm" c="dimmed">
                No differences.
              </Text>
            ) : (
              <SimpleGrid cols={{ base: 2, sm: 4 }}>
                {summaryItems.map(([label, count]) => (
                  <div key={String(label)}>
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
            <ModuleDiffCard key={module.module_id} module={module} attributeName={resolveAttribute} />
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
      )}
    </Stack>
  );
}
