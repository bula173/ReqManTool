import { Anchor, Group, Loader, ScrollArea, Stack, Table, Text, Title } from '@mantine/core';
import { Link, useParams } from 'react-router-dom';
import { moduleReportExportUrl, useExecutionReport } from '../api/queries';
import type { ExecutionReport } from '../api/types';
import { DataSourceLine, ExportMenu, ObjectRef, RefList, ReportCard, ReportError, StatGrid, StatusBadge } from '../components/ReportParts';
import { cellText } from '../components/reportFormat';
import { modulePath } from '../layout/moduleTabs';

function attributeNote(report: ExecutionReport): string {
  const parts: [string, keyof ExecutionReport['attributes']][] = [
    ['result', 'result'],
    ['date', 'execution_date'],
    ['executor', 'executed_by'],
    ['build/version', 'version'],
    ['evidence', 'evidence'],
  ];
  return parts
    .map(([label, role]) => {
      const attribute = report.attributes[role];
      return `${label}: ${attribute ? `“${attribute.name}”` : 'not found'}`;
    })
    .join(' · ');
}

/**
 * Test execution report of a test_execution module (one campaign / build):
 * executed vs planned test cases, pass/fail/blocked counts, the failed and
 * blocked tests with the requirements they affect, builds/versions under test,
 * executors and dates. Computed by the server
 * (`ProjectService.execution_report`); read-only; exports to DOCX / XLSX.
 */
export function ExecutionReportPage() {
  const { projectId, reportModuleId } = useParams<{ projectId: string; reportModuleId: string }>();
  const reportQuery = useExecutionReport(projectId, reportModuleId);

  if (!projectId || !reportModuleId) return null;
  const report = reportQuery.data;
  const counts = report?.test_counts;

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>Test execution report{report ? `: ${report.module.name}` : ''}</Title>
          <Text size="sm" c="dimmed">
            Results of this campaign per test case, from the records linked to the test cases.{' '}
            <Anchor component={Link} to={modulePath(projectId, reportModuleId)} size="sm">
              Open the module
            </Anchor>
          </Text>
        </div>
        <ExportMenu
          disabled={!report}
          formats={[
            { label: 'Word (DOCX)', url: moduleReportExportUrl(projectId, reportModuleId, 'execution', 'docx') },
            { label: 'Excel (XLSX)', url: moduleReportExportUrl(projectId, reportModuleId, 'execution', 'xlsx') },
          ]}
        />
      </Group>

      {reportQuery.isLoading && <Loader size="sm" />}
      {reportQuery.isError && <ReportError error={reportQuery.error} title="Failed to build the report" />}

      {report && counts && (
        <>
          <ReportCard title="Summary">
            <StatGrid
              items={[
                {
                  label: 'Executed of planned',
                  value: `${counts.executed} of ${counts.planned}`,
                  hint: `${counts.executed_percent} %`,
                },
                { label: 'Passed', value: counts.passed, hint: `pass rate ${counts.pass_percent} % of executed` },
                { label: 'Failed', value: counts.failed },
                { label: 'Blocked', value: counts.blocked },
                { label: 'Not run', value: counts.not_run },
                { label: 'Not planned (no record)', value: counts.not_planned, hint: `of ${counts.total} test cases` },
                { label: 'Records', value: report.record_counts.total },
                { label: 'Records linked to no test case', value: report.unlinked_records.length },
              ]}
            />
            <Stack gap={2} mt="sm">
              <Text size="sm">
                <b>Build/version under test:</b> {report.versions.length > 0 ? report.versions.join(', ') : '(not recorded)'}
              </Text>
              <Text size="sm">
                <b>Executed by:</b> {report.executors.length > 0 ? report.executors.join(', ') : '(not recorded)'}
              </Text>
              <Text size="sm">
                <b>Execution dates:</b>{' '}
                {report.date_range
                  ? report.date_range[0] === report.date_range[1]
                    ? report.date_range[0]
                    : `${report.date_range[0]} to ${report.date_range[1]}`
                  : '(not recorded)'}
              </Text>
              <Text size="xs" c="dimmed">
                Test case modules:{' '}
                {report.test_modules.length > 0
                  ? report.test_modules.map((m) => m.name).join(', ')
                  : 'none (no test_cases module is linked to this module)'}
                . Attributes read: {attributeNote(report)}.
              </Text>
            </Stack>
            <DataSourceLine projectId={projectId} source={report.data_source} />
          </ReportCard>

          <ReportCard title={`Failed and blocked tests (${report.problems.length})`}>
            {report.problems.length === 0 ? (
              <Text size="sm" c="dimmed">
                None.
              </Text>
            ) : (
              <Table withTableBorder fz="sm" verticalSpacing={4}>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Test case</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Affected requirements</Table.Th>
                    <Table.Th>Evidence</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {report.problems.map((problem) => (
                    <Table.Tr key={`${problem.module_id}/${problem.id}`}>
                      <Table.Td>
                        <ObjectRef projectId={projectId} moduleId={problem.module_id} id={problem.id} text={problem.text} />
                      </Table.Td>
                      <Table.Td>
                        <StatusBadge status={problem.status} />
                      </Table.Td>
                      <Table.Td>
                        {problem.requirements.length === 0 ? (
                          <Text size="xs" c="dimmed">
                            none linked
                          </Text>
                        ) : (
                          <Group gap={8}>
                            {problem.requirements.map((requirement) => (
                              <ObjectRef
                                key={`${requirement.module_id}/${requirement.id}`}
                                projectId={projectId}
                                moduleId={requirement.module_id}
                                id={requirement.id}
                                text={requirement.text}
                              />
                            ))}
                          </Group>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs">{cellText(problem.latest?.evidence)}</Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </ReportCard>

          <ReportCard title={`Test cases (${report.test_cases.length})`}>
            {report.test_cases.length === 0 ? (
              <Text size="sm" c="dimmed">
                No test cases: link this module to a test_cases module with a link configuration.
              </Text>
            ) : (
              <ScrollArea type="auto" mah="60vh">
                <Table withTableBorder fz="sm" striped verticalSpacing={4}>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Test case</Table.Th>
                      <Table.Th>Status</Table.Th>
                      <Table.Th>Latest record</Table.Th>
                      <Table.Th>Date</Table.Th>
                      <Table.Th>Executed by</Table.Th>
                      <Table.Th>Build/version</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {report.test_cases.map((test) => (
                      <Table.Tr key={`${test.module_id}/${test.id}`}>
                        <Table.Td style={{ maxWidth: 420 }}>
                          <Group gap={6} wrap="nowrap">
                            <ObjectRef projectId={projectId} moduleId={test.module_id} id={test.id} />
                            <Text size="xs" c="dimmed" lineClamp={1}>
                              {test.text}
                            </Text>
                          </Group>
                        </Table.Td>
                        <Table.Td>
                          <StatusBadge status={test.status} />
                        </Table.Td>
                        <Table.Td>
                          {test.latest ? (
                            <ObjectRef
                              projectId={projectId}
                              moduleId={test.latest.module_id}
                              id={test.latest.id}
                              text={test.latest.text}
                            />
                          ) : (
                            <Text size="xs" c="dimmed">
                              {test.records.length > 0 ? `${test.records.length} not run` : '–'}
                            </Text>
                          )}
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs">{cellText(test.latest?.execution_date)}</Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs">{cellText(test.latest?.executed_by)}</Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs">{cellText(test.latest?.version)}</Text>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </ScrollArea>
            )}
          </ReportCard>

          {report.unlinked_records.length > 0 && (
            <ReportCard title="Records linked to no test case">
              <RefList
                projectId={projectId}
                moduleId={report.module.id}
                ids={report.unlinked_records}
                texts={new Map(report.records.map((r) => [r.id, r.text]))}
              />
            </ReportCard>
          )}
        </>
      )}
    </Stack>
  );
}
