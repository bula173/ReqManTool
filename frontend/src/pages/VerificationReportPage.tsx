import { Anchor, Group, Loader, ScrollArea, SegmentedControl, Stack, Table, Text, Title } from '@mantine/core';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { moduleReportExportUrl, useVerificationReport } from '../api/queries';
import type { VerificationReport, VerificationStatus } from '../api/types';
import { ExportMenu, ObjectRef, ReportCard, ReportError, StatGrid, StatusBadge } from '../components/ReportParts';
import { STATUS_LABELS } from '../components/reportFormat';
import { modulePath } from '../layout/moduleTabs';

const STATUSES: VerificationStatus[] = ['verified', 'failed', 'not_run', 'not_covered'];

function AttributeSources({ report }: { report: VerificationReport }) {
  if (report.execution_modules.length === 0) return null;
  return (
    <Stack gap={2} mt="xs">
      {report.execution_modules.map((module) => (
        <Text size="xs" c="dimmed" key={module.id}>
          {module.name}: result from{' '}
          {module.attributes.result ? `“${module.attributes.result.name}”` : '(no result attribute found)'}, date from{' '}
          {module.attributes.execution_date ? `“${module.attributes.execution_date.name}”` : '(no date attribute found)'}
        </Text>
      ))}
    </Stack>
  );
}

/**
 * Verification report of a requirements module: each requirement's covering
 * test cases with their latest execution result, its status (verified /
 * failed / not run / not covered) and the totals. Computed by the server
 * (`ProjectService.verification_report`); read-only; exports to DOCX / XLSX.
 */
export function VerificationReportPage() {
  const { projectId, reportModuleId } = useParams<{ projectId: string; reportModuleId: string }>();
  const reportQuery = useVerificationReport(projectId, reportModuleId);
  const [filter, setFilter] = useState<'all' | VerificationStatus>('all');

  if (!projectId || !reportModuleId) return null;
  const report = reportQuery.data;
  const rows = report ? report.requirements.filter((r) => filter === 'all' || r.status === filter) : [];

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>Verification report{report ? `: ${report.module.name}` : ''}</Title>
          <Text size="sm" c="dimmed">
            Each requirement with the test cases linked to it and their latest execution result.{' '}
            <Anchor component={Link} to={modulePath(projectId, reportModuleId)} size="sm">
              Open the module
            </Anchor>
          </Text>
        </div>
        <ExportMenu
          disabled={!report}
          formats={[
            { label: 'Word (DOCX)', url: moduleReportExportUrl(projectId, reportModuleId, 'verification', 'docx') },
            { label: 'Excel (XLSX)', url: moduleReportExportUrl(projectId, reportModuleId, 'verification', 'xlsx') },
          ]}
        />
      </Group>

      {reportQuery.isLoading && <Loader size="sm" />}
      {reportQuery.isError && <ReportError error={reportQuery.error} title="Failed to build the report" />}

      {report && (
        <>
          <ReportCard title="Summary">
            <StatGrid
              items={STATUSES.map((status) => ({
                label: STATUS_LABELS[status],
                value: `${report.totals[status]} of ${report.totals.total}`,
                hint: `${report.percent[status]} %`,
              }))}
            />
            <Text size="xs" c="dimmed" mt="xs">
              Test case modules:{' '}
              {report.test_modules.length > 0
                ? report.test_modules.map((m) => m.name).join(', ')
                : 'none (no test_cases module is linked to this module)'}
              . Execution modules:{' '}
              {report.execution_modules.length > 0 ? report.execution_modules.map((m) => m.name).join(', ') : 'none'}.
              {report.totals.excluded > 0 && ` ${report.totals.excluded} headings/information objects left out.`}
            </Text>
            <AttributeSources report={report} />
          </ReportCard>

          <ReportCard
            title="Requirements"
            right={
              <SegmentedControl
                size="xs"
                value={filter}
                onChange={(value) => setFilter(value as typeof filter)}
                data={[
                  { value: 'all', label: `All (${report.totals.total})` },
                  ...STATUSES.map((status) => ({
                    value: status,
                    label: `${STATUS_LABELS[status]} (${report.totals[status]})`,
                  })),
                ]}
              />
            }
          >
            {report.requirements.length === 0 ? (
              <Text size="sm" c="dimmed">
                This module has no requirements.
              </Text>
            ) : rows.length === 0 ? (
              <Text size="sm" c="dimmed">
                No requirement has this status.
              </Text>
            ) : (
              <ScrollArea type="auto" mah="70vh">
                <Table withTableBorder fz="sm" striped verticalSpacing={4}>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Requirement</Table.Th>
                      <Table.Th>Status</Table.Th>
                      <Table.Th>Test cases (latest result)</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {rows.map((requirement) => (
                      <Table.Tr key={requirement.id}>
                        <Table.Td style={{ maxWidth: 480 }}>
                          <Group gap={6} wrap="nowrap" pl={requirement.level * 10}>
                            <ObjectRef projectId={projectId} moduleId={report.module.id} id={requirement.id} />
                            <Text size="xs" c="dimmed" lineClamp={2}>
                              {requirement.text}
                            </Text>
                          </Group>
                        </Table.Td>
                        <Table.Td>
                          <StatusBadge status={requirement.status} />
                        </Table.Td>
                        <Table.Td>
                          {requirement.tests.length === 0 ? (
                            <Text size="xs" c="dimmed">
                              –
                            </Text>
                          ) : (
                            <Stack gap={2}>
                              {requirement.tests.map((test) => (
                                <Group gap={6} key={`${test.module_id}/${test.id}`} wrap="nowrap">
                                  <ObjectRef projectId={projectId} moduleId={test.module_id} id={test.id} text={test.text} />
                                  <StatusBadge status={test.result} />
                                  {test.latest ? (
                                    <Text size="xs" c="dimmed">
                                      {test.latest.execution_date ?? 'no date'} ·{' '}
                                      <ObjectRef
                                        projectId={projectId}
                                        moduleId={test.latest.module_id}
                                        id={test.latest.id}
                                        text={test.latest.text}
                                      />
                                    </Text>
                                  ) : (
                                    <Text size="xs" c="dimmed">
                                      {test.source === 'test_case'
                                        ? 'result of the test case'
                                        : test.executions > 0
                                          ? `${test.executions} record(s), none executed`
                                          : 'no execution'}
                                    </Text>
                                  )}
                                </Group>
                              ))}
                            </Stack>
                          )}
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
