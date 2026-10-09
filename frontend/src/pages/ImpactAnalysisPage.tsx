import {
  ActionIcon,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  ScrollArea,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconPlus, IconX } from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  impactExportUrl,
  useImpactAnalysis,
  useModules,
  useModuleTree,
  useRequirements,
} from '../api/queries';
import type { ImpactDirection, ImpactEntry, ImpactReport, Module } from '../api/types';
import { groupedModuleOptions } from '../components/moduleTree';
import { cellText } from '../components/reportFormat';
import { DataSourceLine, ExportMenu, ObjectRef, ReportCard, ReportError, StatGrid } from '../components/ReportParts';
import { richTextToPlain } from '../components/richText';

/** Depth bounds of `ProjectService.impact_analysis` (the server refuses anything else). */
const MAX_DEPTH = 10;
/** The most starting requirements the server accepts (`MAX_IMPACT_SEEDS`). */
const MAX_SEEDS = 100;

const DIRECTIONS: ImpactDirection[] = ['upstream', 'downstream', 'both'];
const DIRECTION_LABELS: Record<ImpactDirection, string> = {
  upstream: 'Upstream',
  downstream: 'Downstream',
  both: 'Both',
};

/** The depth from the URL; anything else falls back to the default (the server would 400). */
function parseDepth(value: string | null): number {
  const parsed = Number(value ?? 3);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= MAX_DEPTH ? parsed : 3;
}

function parseDirection(value: string | null): ImpactDirection {
  return DIRECTIONS.includes(value as ImpactDirection) ? (value as ImpactDirection) : 'both';
}

/** A report attribute value as the tables show it (booleans as Yes/No, nothing as a dash). */
function impactValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return cellText(value);
}

/** The name of the attribute playing `role` in the analysis (the first one found), else the default. */
function roleName(
  report: ImpactReport,
  role: 'sil_attribute' | 'safety_related_attribute',
  fallback: string,
): string {
  const found = report.modules.find((module) => module[role]);
  return (found ? found[role]?.name : null) ?? fallback;
}

function moduleName(report: ImpactReport, moduleId: string): string {
  return report.modules.find((module) => module.id === moduleId)?.name ?? moduleId;
}

/** The test cases linked to a requirement, each opening in its module. */
function TestRefs({ projectId, tests }: { projectId: string; tests: { id: string; module_id: string; text: string }[] }) {
  if (tests.length === 0) {
    return (
      <Text size="xs" c="dimmed">
        –
      </Text>
    );
  }
  return (
    <Group gap={8}>
      {tests.map((test) => (
        <ObjectRef key={test.id} projectId={projectId} moduleId={test.module_id} id={test.id} text={test.text} />
      ))}
    </Group>
  );
}

/** The shared columns of both tables: requirement, module, text, SIL, Safety Related, linked tests. */
function EntryCells({
  report,
  entry,
  projectId,
}: {
  report: ImpactReport;
  entry: ImpactEntry;
  projectId: string;
}) {
  return (
    <>
      <Table.Td>
        <ObjectRef projectId={projectId} moduleId={entry.module_id} id={entry.id} text={entry.text} />
      </Table.Td>
      <Table.Td>{moduleName(report, entry.module_id)}</Table.Td>
      <Table.Td style={{ maxWidth: 360 }}>
        <Text size="xs" lineClamp={2}>
          {entry.text || '–'}
        </Text>
      </Table.Td>
      <Table.Td>{impactValue(entry.sil)}</Table.Td>
      <Table.Td>{impactValue(entry.safety_related)}</Table.Td>
      <Table.Td>
        <TestRefs projectId={projectId} tests={entry.tests} />
      </Table.Td>
    </>
  );
}

function HeaderCells({ report }: { report: ImpactReport }) {
  return (
    <>
      <Table.Th>Requirement</Table.Th>
      <Table.Th>Module</Table.Th>
      <Table.Th>Text</Table.Th>
      <Table.Th>{roleName(report, 'sil_attribute', 'SIL')}</Table.Th>
      <Table.Th>{roleName(report, 'safety_related_attribute', 'Safety Related')}</Table.Th>
      <Table.Th>Linked tests</Table.Th>
    </>
  );
}

/**
 * Impact analysis: pick starting requirements (or arrive with `?req=`, from
 * a requirement's menu), choose how far to walk and in which direction, and
 * see every requirement the links reach — with its distance, the chain it was
 * reached by, its SIL / Safety Related values and its linked tests. Computed
 * by the server (`ProjectService.impact_analysis`), never stored; exports to
 * CSV / XLSX / DOCX with a change-reference field.
 */
export function ImpactAnalysisPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [params, setParams] = useSearchParams();
  const seeds = useMemo(() => params.getAll('req'), [params]);
  const depth = parseDepth(params.get('depth'));
  const direction = parseDirection(params.get('dir'));

  const [moduleId, setModuleId] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);

  const modulesQuery = useModules(projectId);
  const moduleTreeQuery = useModuleTree(projectId);
  const modules = modulesQuery.data ?? [];
  const currentModuleId = moduleId ?? modules[0]?.id ?? null;
  const requirementsQuery = useRequirements(projectId, currentModuleId);
  const reportQuery = useImpactAnalysis(projectId, seeds, depth, direction);

  const update = (key: 'depth' | 'dir', value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };

  const setSeeds = (ids: string[]) => {
    const next = new URLSearchParams(params);
    next.delete('req');
    ids.forEach((id) => next.append('req', id));
    setParams(next, { replace: true });
  };

  const addPicked = () => {
    if (picked.length === 0) return;
    const next = [...seeds];
    picked.forEach((id) => {
      if (!next.includes(id)) next.push(id);
    });
    setSeeds(next.slice(0, MAX_SEEDS));
    setPicked([]);
  };

  if (!projectId) return null;

  const moduleOptions = groupedModuleOptions(moduleTreeQuery.data, modules, (module: Module) => ({
    value: module.id,
    label: module.name,
  }));
  const requirementOptions = (requirementsQuery.data ?? []).map((requirement) => {
    const text = richTextToPlain(requirement.attributes.object_text);
    return { value: requirement.id, label: text ? `${requirement.id} — ${text}` : requirement.id };
  });

  const report = reportQuery.data;
  // Only the report of the current selection (never a stale one while the URL changes).
  const shown =
    report && report.seeds.map((seed) => seed.id).join('\u0000') === seeds.join('\u0000') ? report : undefined;

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>Impact analysis</Title>
        <Text size="sm" c="dimmed">
          Walk the links from the selected requirements to see what a change there would affect.{' '}
          <Anchor component={Link} to={`/projects/${encodeURIComponent(projectId)}`} size="sm">
            Back to the project
          </Anchor>
        </Text>
      </div>

      <Card withBorder>
        <Stack gap="xs">
          <Group align="flex-end" gap="xs" wrap="wrap">
            <Select
              label="Module"
              placeholder={modulesQuery.isLoading ? 'Loading…' : 'No modules'}
              data={moduleOptions}
              value={currentModuleId}
              onChange={(value) => {
                setModuleId(value);
                setPicked([]);
              }}
              disabled={modules.length === 0}
              searchable
              w={240}
            />
            <Select
              label="Requirements of this module"
              placeholder={requirementsQuery.isLoading ? 'Loading…' : 'Pick requirements to add'}
              data={requirementOptions}
              value={null}
              onChange={(value) => value && setPicked((current) => (current.includes(value) ? current : [...current, value]))}
              disabled={!currentModuleId || requirementOptions.length === 0}
              searchable
              nothingFoundMessage="No requirement matches"
              w={420}
            />
            <Button
              leftSection={<IconPlus size={14} />}
              onClick={addPicked}
              disabled={picked.length === 0 || seeds.length >= MAX_SEEDS}
            >
              Add{picked.length > 0 ? ` (${picked.length})` : ''}
            </Button>
            <NumberInput
              label="Depth"
              description={`1–${MAX_DEPTH}`}
              value={depth}
              onChange={(value) => update('depth', String(value))}
              min={1}
              max={MAX_DEPTH}
              allowDecimal={false}
              w={110}
            />
            <div>
              <Text size="xs" fw={500} mb={4}>
                Direction
              </Text>
              <SegmentedControl
                value={direction}
                onChange={(value) => update('dir', value)}
                data={DIRECTIONS.map((value) => ({ value, label: DIRECTION_LABELS[value] }))}
              />
            </div>
            <ExportMenu
              disabled={seeds.length === 0 || !shown}
              formats={[
                { label: 'CSV', url: impactExportUrl(projectId, seeds, depth, direction, 'csv') },
                { label: 'Excel (XLSX)', url: impactExportUrl(projectId, seeds, depth, direction, 'xlsx') },
                { label: 'Word (DOCX)', url: impactExportUrl(projectId, seeds, depth, direction, 'docx') },
              ]}
            />
          </Group>

          {seeds.length > 0 ? (
            <Group gap={6}>
              {seeds.map((id) => (
                <Badge
                  key={id}
                  size="sm"
                  variant="light"
                  ff="monospace"
                  rightSection={
                    <Tooltip label="Remove from the analysis">
                      <ActionIcon
                        size="xs"
                        variant="transparent"
                        color="gray"
                        aria-label={`Remove ${id}`}
                        onClick={() => setSeeds(seeds.filter((seed) => seed !== id))}
                      >
                        <IconX size={12} />
                      </ActionIcon>
                    </Tooltip>
                  }
                >
                  {id}
                </Badge>
              ))}
            </Group>
          ) : (
            <Text size="xs" c="dimmed">
              No starting requirement yet — pick some above, or use “Impact analysis” in a requirement's menu.
            </Text>
          )}
          {seeds.length >= MAX_SEEDS && (
            <Text size="xs" c="orange">
              The analysis starts from at most {MAX_SEEDS} requirements.
            </Text>
          )}
        </Stack>
        {modulesQuery.isError && <ReportError error={modulesQuery.error} title="Failed to load the modules" />}
        {requirementsQuery.isError && (
          <ReportError error={requirementsQuery.error} title="Failed to load the requirements" />
        )}
      </Card>

      {seeds.length === 0 && (
        <Text size="sm" c="dimmed">
          Pick at least one starting requirement to run the analysis.
        </Text>
      )}
      {reportQuery.isLoading && <Loader size="sm" />}
      {reportQuery.isError && <ReportError error={reportQuery.error} title="Failed to build the impact analysis" />}

      {shown && (
        <>
          <ReportCard title="Summary">
            <StatGrid
              items={[
                { label: 'Starting requirements', value: shown.summary.seeds },
                { label: 'Requirements reached', value: shown.summary.reached },
                { label: 'Upstream', value: shown.summary.upstream },
                { label: 'Downstream', value: shown.summary.downstream },
              ]}
            />
            <Text size="xs" c="dimmed" mt="xs">
              {DIRECTION_LABELS[shown.direction]} walk over at most {shown.depth} step(s)
              {shown.summary.max_distance > 0 && `, deepest ${shown.summary.max_distance} step(s) away`}. Headings,
              information and definitions are left out. SIL comes from “
              {roleName(shown, 'sil_attribute', 'SIL')}” and Safety Related from “
              {roleName(shown, 'safety_related_attribute', 'Safety Related')}” where a module has such an attribute.
            </Text>
            <DataSourceLine projectId={projectId} source={shown.data_source} />
          </ReportCard>

          <ReportCard title="Starting requirements">
            <ScrollArea type="auto" mah="40vh">
              <Table withTableBorder fz="sm" striped verticalSpacing={4}>
                <Table.Thead>
                  <Table.Tr>
                    <HeaderCells report={shown} />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {shown.seeds.map((seed) => (
                    <Table.Tr key={seed.id}>
                      <EntryCells report={shown} entry={seed} projectId={projectId} />
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </ReportCard>

          <ReportCard title={`Affected requirements (${shown.results.length})`}>
            {shown.results.length === 0 ? (
              <Text size="sm" c="dimmed">
                Nothing is linked to the selected requirements in this direction within {shown.depth} step(s).
              </Text>
            ) : (
              <ScrollArea type="auto" mah="70vh">
                <Table withTableBorder fz="sm" striped verticalSpacing={4}>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Direction</Table.Th>
                      <Table.Th>Distance</Table.Th>
                      <HeaderCells report={shown} />
                      <Table.Th>Reached by</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {shown.results.map((row) => (
                      <Table.Tr key={`${row.direction}:${row.id}`}>
                        <Table.Td>
                          <Badge size="xs" variant="light" color={row.direction === 'upstream' ? 'blue' : 'orange'}>
                            {DIRECTION_LABELS[row.direction]}
                          </Badge>
                        </Table.Td>
                        <Table.Td>{row.distance}</Table.Td>
                        <EntryCells report={shown} entry={row} projectId={projectId} />
                        <Table.Td>
                          <Text size="xs" ff="monospace" style={{ whiteSpace: 'nowrap' }}>
                            {row.path.join(' → ')}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </ScrollArea>
            )}
          </ReportCard>
        </>
      )}
    </Stack>
  );
}
