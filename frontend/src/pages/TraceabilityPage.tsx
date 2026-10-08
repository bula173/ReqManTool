import {
  ActionIcon,
  Anchor,
  Card,
  Group,
  Loader,
  ScrollArea,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconArrowRight, IconX } from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { traceabilityExportUrl, useLinkConfigurations, useModules, useTraceabilityMatrix } from '../api/queries';
import type { LinkConfiguration, Module, TraceabilityReport } from '../api/types';
import { ExportMenu, ObjectRef, RefList, ReportCard, ReportError, StatGrid } from '../components/ReportParts';

/** Grids larger than this are not drawn (the XLSX export has them). */
const MAX_GRID_COLUMNS = 200;
const MAX_GRID_ROWS = 1000;

/**
 * The module sequence of a chain of link configurations, as the server
 * orients it (`ProjectService.traceability_matrix`): it starts at the first
 * configuration's target module unless the second configuration only joins
 * its source module. Null when a configuration does not continue the chain.
 */
function chainModules(configs: LinkConfiguration[]): string[] | null {
  if (configs.length === 0) return [];
  const [first, second] = configs;
  let chain = [first.target_module_id, first.source_module_id];
  if (second) {
    const ends = [second.source_module_id, second.target_module_id];
    if (!ends.includes(chain[1]) && ends.includes(chain[0])) chain = [chain[1], chain[0]];
  }
  for (const config of configs.slice(1)) {
    const end = chain[chain.length - 1];
    if (config.target_module_id === end) chain.push(config.source_module_id);
    else if (config.source_module_id === end) chain.push(config.target_module_id);
    else return null;
  }
  return chain;
}

function configLabel(config: LinkConfiguration, modules: Map<string, Module>): string {
  const name = (id: string) => modules.get(id)?.name ?? id;
  return `${config.name} (${name(config.source_module_id)} → ${name(config.target_module_id)})`;
}

function MatrixGrid({ report, stepIndex }: { report: TraceabilityReport; stepIndex: number }) {
  const step = report.steps[stepIndex];
  const rows = report.modules[stepIndex].items;
  const columns = report.modules[stepIndex + 1].items;
  const linked = useMemo(() => new Set(step.links.map(([row, column]) => `${row}\u0000${column}`)), [step]);
  if (rows.length === 0 || columns.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        {rows.length === 0 ? report.modules[stepIndex].name : report.modules[stepIndex + 1].name} has no
        requirements to show.
      </Text>
    );
  }
  if (columns.length > MAX_GRID_COLUMNS || rows.length > MAX_GRID_ROWS) {
    return (
      <Text size="sm" c="dimmed">
        The grid ({rows.length} × {columns.length}) is too large to draw here; export it to XLSX.
      </Text>
    );
  }
  const uncovered = new Set(step.uncovered);
  const orphans = new Set(step.orphans);
  return (
    <ScrollArea type="auto" mah="70vh">
      <Table withTableBorder withColumnBorders striped={false} fz="xs" style={{ width: 'max-content' }}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th style={{ position: 'sticky', left: 0, background: 'var(--mantine-color-body)', zIndex: 2 }}>
              {report.modules[stepIndex].name} \ {report.modules[stepIndex + 1].name}
            </Table.Th>
            {columns.map((column) => (
              <Table.Th
                key={column.id}
                style={{ writingMode: 'vertical-rl', whiteSpace: 'nowrap', verticalAlign: 'bottom', padding: '6px 2px' }}
                c={orphans.has(column.id) ? 'orange' : undefined}
              >
                <Tooltip label={column.text || column.id} multiline maw={420} openDelay={300} withinPortal>
                  <span>{column.id}</span>
                </Tooltip>
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr key={row.id}>
              <Table.Td
                style={{ position: 'sticky', left: 0, background: 'var(--mantine-color-body)', zIndex: 1, maxWidth: 360 }}
              >
                <Group gap={6} wrap="nowrap" pl={row.level * 10}>
                  <ObjectRef projectId={report.project_id} moduleId={step.from_module_id} id={row.id} />
                  <Text size="xs" c={uncovered.has(row.id) ? 'orange' : 'dimmed'} truncate="end">
                    {row.text}
                  </Text>
                </Group>
              </Table.Td>
              {columns.map((column) => (
                <Table.Td key={column.id} ta="center" p={2}>
                  {linked.has(`${row.id}\u0000${column.id}`) ? '●' : ''}
                </Table.Td>
              ))}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  );
}

function TraceTable({ report }: { report: TraceabilityReport }) {
  const later = report.modules.slice(1);
  return (
    <ScrollArea type="auto" mah="60vh">
      <Table withTableBorder fz="xs" striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{report.modules[0].name}</Table.Th>
            {later.map((module) => (
              <Table.Th key={module.id}>{module.name}</Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {report.trace.map((row) => (
            <Table.Tr key={row.id}>
              <Table.Td>
                <ObjectRef
                  projectId={report.project_id}
                  moduleId={report.modules[0].id}
                  id={row.id}
                  text={report.modules[0].items.find((item) => item.id === row.id)?.text}
                />
              </Table.Td>
              {row.reached.map((ids, index) => (
                <Table.Td key={later[index].id}>
                  <RefList projectId={report.project_id} moduleId={later[index].id} ids={ids} empty="–" />
                </Table.Td>
              ))}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  );
}

/**
 * Traceability matrix: pick a link configuration, optionally extend it to a
 * chain (StRS → SyRS → SwRS → SwTest → SwTestExec), and see each step's
 * source × target grid, coverage, uncovered and orphan objects, the whole
 * chain's trace and the unverified requirements. The chain is kept in the
 * URL (`?config=a&config=b`). Read-only; exports to CSV / XLSX.
 */
export function TraceabilityPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [params, setParams] = useSearchParams();
  const configIds = params.getAll('config');
  const configsQuery = useLinkConfigurations(projectId);
  const modulesQuery = useModules(projectId);
  const reportQuery = useTraceabilityMatrix(projectId, configIds);
  const [stepChoice, setStepChoice] = useState(0);

  const modules = useMemo(() => new Map((modulesQuery.data ?? []).map((m) => [m.id, m])), [modulesQuery.data]);
  // Cross-project configurations cannot be part of a chain yet (the server refuses them).
  const configs = useMemo(() => (configsQuery.data ?? []).filter((c) => !c.target_project_id), [configsQuery.data]);
  const byId = useMemo(() => new Map(configs.map((c) => [c.id, c])), [configs]);

  if (!projectId) return null;

  const chainConfigs = configIds.map((id) => byId.get(id)).filter((c): c is LinkConfiguration => c !== undefined);
  const chain = chainConfigs.length === configIds.length ? chainModules(chainConfigs) : null;
  const setChain = (ids: string[]) => {
    const next = new URLSearchParams(params);
    next.delete('config');
    ids.forEach((id) => next.append('config', id));
    setParams(next, { replace: true });
  };

  // The configurations that may extend the chain: they must join its last
  // module (with one configuration, either of its modules).
  const ends = chain === null ? [] : chain.length === 2 ? chain : chain.slice(-1);
  const nextOptions = configs
    .filter((c) => !configIds.includes(c.id))
    .filter((c) => configIds.length === 0 || ends.includes(c.source_module_id) || ends.includes(c.target_module_id))
    .map((c) => ({ value: c.id, label: configLabel(c, modules) }));

  const report = reportQuery.data;
  // Only the current chain's report (never a stale one while the URL changes).
  const shown =
    report && report.steps.map((step) => step.config.id).join('\u0000') === configIds.join('\u0000') ? report : undefined;
  const stepIndex = shown ? Math.min(stepChoice, shown.steps.length - 1) : 0;
  const moduleName = (id: string) => shown?.modules.find((m) => m.id === id)?.name ?? modules.get(id)?.name ?? id;
  const texts = new Map((shown?.modules ?? []).flatMap((m) => m.items.map((item) => [item.id, item.text] as const)));

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>Traceability matrix</Title>
        <Text size="sm" c="dimmed">
          Pick a link configuration, or a chain of them, to see which requirements are covered downstream.{' '}
          <Anchor component={Link} to={`/projects/${encodeURIComponent(projectId)}`} size="sm">
            Back to the project
          </Anchor>
        </Text>
      </div>

      <Card withBorder>
        <Group align="flex-end" gap="xs" wrap="wrap">
          {chainConfigs.map((config, index) => (
            <Group key={config.id} gap={4} wrap="nowrap">
              {index > 0 && <IconArrowRight size={16} />}
              <Text size="sm" fw={500}>
                {configLabel(config, modules)}
              </Text>
              {index === chainConfigs.length - 1 && (
                <Tooltip label="Remove this step">
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    color="gray"
                    aria-label="Remove this step"
                    onClick={() => setChain(configIds.slice(0, -1))}
                  >
                    <IconX size={14} />
                  </ActionIcon>
                </Tooltip>
              )}
            </Group>
          ))}
          <Select
            label={configIds.length === 0 ? 'Link configuration' : 'Continue the chain'}
            placeholder={
              configsQuery.isLoading
                ? 'Loading…'
                : nextOptions.length === 0
                  ? configIds.length === 0
                    ? 'This project has no link configurations'
                    : 'No configuration continues the chain'
                  : 'Pick a link configuration'
            }
            data={nextOptions}
            value={null}
            onChange={(value) => value && setChain([...configIds, value])}
            disabled={nextOptions.length === 0 || chain === null}
            searchable
            w={360}
          />
          <ExportMenu
            disabled={configIds.length === 0 || !shown}
            formats={[
              { label: 'CSV (trace table)', url: traceabilityExportUrl(projectId, configIds, 'csv') },
              { label: 'Excel (XLSX: summary, grids, gaps)', url: traceabilityExportUrl(projectId, configIds, 'xlsx') },
            ]}
          />
        </Group>
        {chain && chain.length > 0 && (
          <Text size="xs" c="dimmed" mt="xs">
            Modules: {chain.map((id) => modules.get(id)?.name ?? id).join(' → ')}
          </Text>
        )}
        {configsQuery.isError && <ReportError error={configsQuery.error} title="Failed to load link configurations" />}
      </Card>

      {configIds.length === 0 && (
        <Text size="sm" c="dimmed">
          Pick a link configuration to build the matrix.
        </Text>
      )}
      {reportQuery.isLoading && <Loader size="sm" />}
      {reportQuery.isError && <ReportError error={reportQuery.error} title="Failed to build the matrix" />}

      {shown && (
        <>
          <ReportCard title="Coverage">
            <StatGrid
              items={[
                {
                  label: `${shown.modules[0].name} covered by ${shown.modules[1].name}`,
                  value: `${shown.summary.covered} of ${shown.summary.total}`,
                  hint: `${shown.summary.covered_percent} %`,
                },
                ...(shown.steps.length > 1
                  ? [
                      {
                        label: `${shown.modules[0].name} traced to ${shown.modules[shown.modules.length - 1].name}`,
                        value: `${shown.summary.traced} of ${shown.summary.total}`,
                        hint: `${shown.summary.traced_percent} %`,
                      },
                    ]
                  : []),
                ...shown.steps.slice(1).map((step) => ({
                  label: `${moduleName(step.from_module_id)} covered by ${moduleName(step.to_module_id)}`,
                  value: `${step.covered} of ${step.total}`,
                  hint: `${step.covered_percent} %`,
                })),
                ...shown.unverified.map((entry) => ({
                  label: `${moduleName(entry.module_id)} unverified (no ${moduleName(entry.test_module_id)} test)`,
                  value: `${entry.ids.length} of ${entry.total}`,
                })),
              ]}
            />
            {shown.modules.some((m) => m.excluded > 0) && (
              <Text size="xs" c="dimmed" mt="xs">
                Headings, information and definitions are left out (
                {shown.modules
                  .filter((m) => m.excluded > 0)
                  .map((m) => `${m.name}: ${m.excluded}`)
                  .join(', ')}
                ).
              </Text>
            )}
          </ReportCard>

          <ReportCard
            title="Matrix"
            right={
              shown.steps.length > 1 ? (
                <SegmentedControl
                  size="xs"
                  value={String(stepIndex)}
                  onChange={(value) => setStepChoice(Number(value))}
                  data={shown.steps.map((step, index) => ({
                    value: String(index),
                    label: `${moduleName(step.from_module_id)} × ${moduleName(step.to_module_id)}`,
                  }))}
                />
              ) : undefined
            }
          >
            <Text size="xs" c="dimmed" mb="xs">
              ● linked by “{shown.steps[stepIndex].config.name}”. Orange row: not covered; orange column: orphan.
            </Text>
            <MatrixGrid report={shown} stepIndex={stepIndex} />
          </ReportCard>

          {shown.steps.length > 1 && (
            <ReportCard title="Chain trace">
              <TraceTable report={shown} />
            </ReportCard>
          )}

          {shown.steps.map((step) => (
            <ReportCard key={step.config.id} title={`Gaps: ${step.config.name}`}>
              <Stack gap="xs">
                <div>
                  <Text size="sm" fw={500}>
                    {moduleName(step.from_module_id)} not covered by {moduleName(step.to_module_id)} (
                    {step.uncovered.length})
                  </Text>
                  <RefList projectId={projectId} moduleId={step.from_module_id} ids={step.uncovered} texts={texts} />
                </div>
                <div>
                  <Text size="sm" fw={500}>
                    Orphans: {moduleName(step.to_module_id)} with no link to {moduleName(step.from_module_id)} (
                    {step.orphans.length})
                  </Text>
                  <RefList projectId={projectId} moduleId={step.to_module_id} ids={step.orphans} texts={texts} />
                </div>
              </Stack>
            </ReportCard>
          ))}

          {shown.unverified.map((entry) => (
            <ReportCard key={entry.module_id} title={`Unverified: ${moduleName(entry.module_id)}`}>
              <Text size="xs" c="dimmed" mb="xs">
                Requirements whose trace reaches no {moduleName(entry.test_module_id)} test case.
              </Text>
              <RefList projectId={projectId} moduleId={entry.module_id} ids={entry.ids} texts={texts} />
            </ReportCard>
          ))}
        </>
      )}
    </Stack>
  );
}
