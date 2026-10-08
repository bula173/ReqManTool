import {
  Alert,
  Badge,
  Button,
  FileInput,
  Group,
  List,
  Loader,
  Modal,
  ScrollArea,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertCircle, IconAlertTriangle, IconFileImport } from '@tabler/icons-react';
import { useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { useImportTestResults, usePreviewTestResults } from '../api/queries';
import type { TestResultRow, TestResultsImportOptions, TestResultsPreview } from '../api/types';
import { StatusBadge } from './ReportParts';

interface ImportTestResultsModalProps {
  onClose: () => void;
  projectId: string;
  moduleId: string;
  moduleName: string;
}

/** The options that change how results match (they re-run the preview); the others only fill attributes. */
interface MatchOptions {
  configId: string | null;
  matchAttribute: string | null;
  executionDate: string;
}

/** Most result rows / uncovered test cases listed in the preview (the summary counts them all). */
const MAX_PREVIEW_ROWS = 300;
const MAX_NOT_IN_FILE = 100;

function MatchCell({ row }: { row: TestResultRow }) {
  if (row.state === 'matched') {
    return (
      <Group gap={4} wrap="nowrap">
        <Text size="sm" fw={600}>
          {row.test_case_id}
        </Text>
        {row.match === 'partial' && (
          <Text size="xs" c="dimmed">
            (in name)
          </Text>
        )}
      </Group>
    );
  }
  if (row.state === 'ambiguous') {
    return (
      <Text size="sm" c="orange">
        Ambiguous: {row.candidates.join(', ')}
      </Text>
    );
  }
  return (
    <Text size="sm" c="dimmed">
      No match (skipped)
    </Text>
  );
}

function PreviewSummary({ preview }: { preview: TestResultsPreview }) {
  const { summary } = preview;
  return (
    <Group gap="xs">
      <Badge variant="light">{preview.format === 'robot' ? 'Robot Framework' : 'JUnit XML'}</Badge>
      <Badge variant="light" color="blue">
        {summary.total} result(s)
      </Badge>
      <Badge variant="light" color="green">
        {summary.matched} matched
      </Badge>
      {summary.unmatched > 0 && (
        <Badge variant="light" color="gray">
          {summary.unmatched} unmatched
        </Badge>
      )}
      {summary.ambiguous > 0 && (
        <Badge variant="light" color="orange">
          {summary.ambiguous} ambiguous
        </Badge>
      )}
      <Text size="xs" c="dimmed">
        {summary.passed} passed · {summary.failed} failed · {summary.not_run} not run
      </Text>
    </Group>
  );
}

/**
 * File ▸ Import ▸ Test results / module menu "Import test results": JUnit XML
 * or Robot Framework output.xml results become execution records of a
 * test_execution module, linked to the matched test cases. The server
 * previews the match (read-only) whenever the file or a matching option
 * changes; Import writes exactly that, or nothing when there are errors.
 */
export function ImportTestResultsModal({ onClose, projectId, moduleId, moduleName }: ImportTestResultsModalProps) {
  const previewMutation = usePreviewTestResults(projectId, moduleId);
  const importMutation = useImportTestResults(projectId, moduleId);
  const [file, setFile] = useState<File | null>(null);
  const [matchOptions, setMatchOptions] = useState<MatchOptions>({
    configId: null,
    matchAttribute: null,
    executionDate: '',
  });
  const [executedBy, setExecutedBy] = useState('');
  const [version, setVersion] = useState('');
  const [preview, setPreview] = useState<TestResultsPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  // Only the latest preview request may set the preview (options can change while one runs).
  const previewToken = useRef(0);

  const pending = importMutation.isPending;
  const previewing = previewMutation.isPending;

  const requestOptions = (match: MatchOptions, chosenFile: File): TestResultsImportOptions => ({
    config_id: match.configId ?? undefined,
    match_attribute: match.matchAttribute ?? undefined,
    execution_date: match.executionDate || undefined,
    file_name: chosenFile.name,
  });

  const runPreview = (chosenFile: File | null, match: MatchOptions) => {
    const token = ++previewToken.current;
    setPreview(null);
    setPreviewError(null);
    setImportError(null);
    if (!chosenFile) return;
    previewMutation
      .mutateAsync({ file: chosenFile, options: requestOptions(match, chosenFile) })
      .then((result) => {
        if (token === previewToken.current) setPreview(result);
      })
      .catch((err: unknown) => {
        if (token === previewToken.current) {
          setPreviewError(err instanceof ApiError ? err.message : 'Failed to read the result file');
        }
      });
  };

  const changeMatch = (update: Partial<MatchOptions>) => {
    const next = { ...matchOptions, ...update };
    setMatchOptions(next);
    runPreview(file, next);
  };

  const handleClose = () => {
    if (pending) return;
    previewToken.current++;
    onClose();
  };

  const handleImport = () => {
    if (!file || !preview) return;
    setImportError(null);
    importMutation.mutate(
      {
        file,
        options: {
          ...requestOptions(matchOptions, file),
          // The preview's choices, so the import writes what was shown.
          config_id: preview.config.id,
          match_attribute: preview.match_attribute,
          executed_by: executedBy.trim() || undefined,
          version: version.trim() || undefined,
        },
      },
      {
        onSuccess: (result) => {
          const skipped = result.skipped ? ` ${result.skipped} result(s) skipped.` : '';
          notifications.show({
            message: `Imported ${result.imported} execution record(s) into "${moduleName}", linked to their test cases.${skipped}`,
            color: 'green',
          });
          onClose();
        },
        onError: (err) => setImportError(err instanceof ApiError ? err.message : 'Failed to import the test results'),
      },
    );
  };

  const canImport =
    !!file && !!preview && !previewing && !pending && preview.errors.length === 0 && preview.summary.matched > 0;
  const roleName = (role: keyof TestResultsPreview['attributes']) => preview?.attributes[role]?.name ?? null;

  return (
    <Modal opened onClose={handleClose} title={`Import test results into "${moduleName}"`} centered size="xl">
      <Stack gap="sm">
        <Text size="sm">
          A JUnit XML file or a Robot Framework <code>output.xml</code>. Each result that matches one test case becomes
          a new execution record of this module (result, execution date, executed by, build/version and evidence),
          linked to that test case.
        </Text>
        <List size="sm" spacing={2}>
          <List.Item>
            A result matches a test case whose match value equals its name, qualified name, a Robot tag or a JUnit
            property value, or appears as whole words in its name (e.g. <code>SWT-0001 Login works</code>).
          </List.Item>
          <List.Item>Results matching no or several test cases are skipped. Invalid values reject the whole file.</List.Item>
        </List>
        <FileInput
          label="Result file"
          placeholder="Choose a JUnit .xml or Robot output.xml file"
          accept=".xml,application/xml,text/xml"
          value={file}
          onChange={(value) => {
            setFile(value);
            runPreview(value, matchOptions);
          }}
          clearable
          disabled={pending}
        />
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          <Select
            label="Link to test cases via"
            description={preview ? `Test cases of "${preview.test_module.name}"` : undefined}
            data={(preview?.link_configurations ?? []).map((c) => ({
              value: c.id,
              label: `${c.name} (${c.test_module.name})`,
            }))}
            value={matchOptions.configId ?? preview?.config.id ?? null}
            onChange={(value) => changeMatch({ configId: value, matchAttribute: null })}
            disabled={!preview || pending || (preview.link_configurations.length ?? 0) < 2}
            allowDeselect={false}
            placeholder={file ? '…' : 'Choose a file first'}
          />
          <Select
            label="Match test cases on"
            description="Requirement id or a test case attribute"
            data={(preview?.match_options ?? []).map((o) => ({ value: o.id, label: o.name }))}
            value={matchOptions.matchAttribute ?? preview?.match_attribute ?? null}
            onChange={(value) => changeMatch({ matchAttribute: value })}
            disabled={!preview || pending}
            allowDeselect={false}
            placeholder={file ? '…' : 'Choose a file first'}
          />
          <TextInput
            label="Execution date"
            description="Empty: the date in the file, else today"
            type="date"
            value={matchOptions.executionDate}
            onChange={(e) => changeMatch({ executionDate: e.currentTarget.value })}
            disabled={pending}
          />
          <TextInput
            label="Build / version under test"
            description={preview && !roleName('version') ? 'Not stored: this module has no such attribute' : undefined}
            value={version}
            onChange={(e) => setVersion(e.currentTarget.value)}
            disabled={pending}
          />
          <TextInput
            label="Executed by"
            description={
              preview && !roleName('executed_by') ? 'Not stored: this module has no such attribute' : 'Optional'
            }
            value={executedBy}
            onChange={(e) => setExecutedBy(e.currentTarget.value)}
            disabled={pending}
          />
        </SimpleGrid>

        {previewing && (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm" c="dimmed">
              Matching results…
            </Text>
          </Group>
        )}
        {previewError && (
          <Alert color="red" icon={<IconAlertCircle size={16} />} title="Cannot import this file">
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {previewError}
            </Text>
          </Alert>
        )}
        {preview && (
          <>
            <PreviewSummary preview={preview} />
            {preview.errors.length > 0 && (
              <Alert color="red" icon={<IconAlertCircle size={16} />} title="The import would be refused">
                <List size="sm" spacing={2}>
                  {preview.errors.map((error) => (
                    <List.Item key={error}>{error}</List.Item>
                  ))}
                </List>
              </Alert>
            )}
            {preview.warnings.length > 0 && (
              <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
                <List size="sm" spacing={2}>
                  {preview.warnings.map((warning) => (
                    <List.Item key={warning}>{warning}</List.Item>
                  ))}
                </List>
              </Alert>
            )}
            {preview.results.length === 0 ? (
              <Text size="sm" c="dimmed">
                The file holds no test results.
              </Text>
            ) : (
              <ScrollArea.Autosize mah={280}>
                <Table striped withTableBorder stickyHeader>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Test</Table.Th>
                      <Table.Th>Result</Table.Th>
                      <Table.Th>Date</Table.Th>
                      <Table.Th>Test case</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {preview.results.slice(0, MAX_PREVIEW_ROWS).map((row) => (
                      <Table.Tr key={row.index}>
                        <Table.Td>
                          <Text size="sm" title={row.message || row.full_name}>
                            {row.name || row.full_name}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <StatusBadge status={row.status} />
                        </Table.Td>
                        <Table.Td>
                          <Text size="sm">{row.execution_date}</Text>
                        </Table.Td>
                        <Table.Td>
                          <MatchCell row={row} />
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
                {preview.results.length > MAX_PREVIEW_ROWS && (
                  <Text size="xs" c="dimmed" mt={4}>
                    … and {preview.results.length - MAX_PREVIEW_ROWS} more result(s)
                  </Text>
                )}
              </ScrollArea.Autosize>
            )}
            {preview.test_cases_not_in_file.length > 0 && (
              <Text size="sm" c="dimmed">
                {preview.test_cases_not_in_file.length} test case(s) of "{preview.test_module.name}" have no result in
                this file:{' '}
                {preview.test_cases_not_in_file
                  .slice(0, MAX_NOT_IN_FILE)
                  .map((t) => t.id)
                  .join(', ')}
                {preview.test_cases_not_in_file.length > MAX_NOT_IN_FILE ? ', …' : ''}
              </Text>
            )}
          </>
        )}
        {importError && (
          <Alert color="red" icon={<IconAlertCircle size={16} />} title="Import failed">
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {importError}
            </Text>
          </Alert>
        )}
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={handleClose} disabled={pending}>
            Cancel
          </Button>
          <Button
            leftSection={<IconFileImport size={16} />}
            onClick={handleImport}
            disabled={!canImport}
            loading={pending}
          >
            Import {preview && preview.summary.matched > 0 ? `${preview.summary.matched} result(s)` : ''}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
