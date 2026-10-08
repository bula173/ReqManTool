import {
  Alert,
  Badge,
  Button,
  FileInput,
  Group,
  List,
  Modal,
  ScrollArea,
  SegmentedControl,
  Stack,
  Table,
  Text,
  Textarea,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertCircle, IconEye, IconFileImport } from '@tabler/icons-react';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useImportCsv, usePreviewCsvImport, type CsvSource } from '../api/queries';
import type { CsvImportMode, CsvImportPreview } from '../api/types';

export type CsvInputKind = 'file' | 'paste';

interface ImportCsvModalProps {
  opened: boolean;
  onClose: () => void;
  projectId: string;
  moduleId: string;
  moduleName: string;
  /** `paste`: open with the paste-text box (Paste AI result). Default: file upload. */
  initialInput?: CsvInputKind;
  initialMode?: CsvImportMode;
}

const MAX_PREVIEW_ROWS = 200;

function shown(value: unknown): string {
  if (value === null || value === undefined || value === '') return '(empty)';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

function short(value: unknown, length = 120): string {
  const text = shown(value);
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

function PreviewSummary({ preview }: { preview: CsvImportPreview }) {
  const newLinks = preview.links.filter((link) => !link.exists);
  const nothing = preview.created.length === 0 && preview.updated.length === 0 && newLinks.length === 0;
  return (
    <Stack gap="xs">
      <Group gap="xs">
        <Badge color="green" variant="light">{preview.created.length} new</Badge>
        {preview.mode === 'update' && (
          <>
            <Badge color="blue" variant="light">{preview.updated.length} changed</Badge>
            <Badge color="gray" variant="light">{preview.unchanged.length} unchanged</Badge>
          </>
        )}
        {preview.links.length > 0 && (
          <Badge color="grape" variant="light">
            {newLinks.length} link(s){preview.links.length > newLinks.length ? `, ${preview.links.length - newLinks.length} already there` : ''}
          </Badge>
        )}
      </Group>
      {nothing && <Text size="sm" c="dimmed">The file changes nothing.</Text>}
      {preview.ignored_columns.length > 0 && (
        <Text size="xs" c="dimmed">Ignored system column(s): {preview.ignored_columns.join(', ')}</Text>
      )}
      <ScrollArea.Autosize mah={320} type="auto">
        <Stack gap="sm">
          {preview.updated.length > 0 && (
            <Table striped withTableBorder fz="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Requirement</Table.Th>
                  <Table.Th>Attribute</Table.Th>
                  <Table.Th>Old</Table.Th>
                  <Table.Th>New</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {preview.updated.slice(0, MAX_PREVIEW_ROWS).flatMap((update) =>
                  update.changes.map((change, index) => (
                    <Table.Tr key={`${update.id}/${change.attribute_id}`}>
                      <Table.Td>{index === 0 ? update.id : ''}</Table.Td>
                      <Table.Td>{change.name}</Table.Td>
                      <Table.Td style={{ textDecoration: 'line-through', whiteSpace: 'pre-wrap' }}>{short(change.old, 300)}</Table.Td>
                      <Table.Td style={{ whiteSpace: 'pre-wrap' }}>{short(change.new, 300)}</Table.Td>
                    </Table.Tr>
                  )),
                )}
              </Table.Tbody>
            </Table>
          )}
          {preview.created.length > 0 && (
            <Table striped withTableBorder fz="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Row</Table.Th>
                  <Table.Th>New id (provisional)</Table.Th>
                  <Table.Th>Parent</Table.Th>
                  <Table.Th>ObjectText</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {preview.created.slice(0, MAX_PREVIEW_ROWS).map((item) => (
                  <Table.Tr key={item.row}>
                    <Table.Td>{item.row}</Table.Td>
                    <Table.Td>{item.id}</Table.Td>
                    <Table.Td>{item.parent_id ?? ''}</Table.Td>
                    <Table.Td>{short(item.attributes.object_text)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
          {preview.links.length > 0 && (
            <Table striped withTableBorder fz="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Link</Table.Th>
                  <Table.Th>From</Table.Th>
                  <Table.Th>To</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {preview.links.slice(0, MAX_PREVIEW_ROWS).map((link) => (
                  <Table.Tr key={`${link.config_id}/${link.source_id}/${link.target_id}`}>
                    <Table.Td>{link.config_name}</Table.Td>
                    <Table.Td>{link.source_id}</Table.Td>
                    <Table.Td>{link.target_id}</Table.Td>
                    <Table.Td>{link.exists ? 'already linked' : ''}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Stack>
      </ScrollArea.Autosize>
    </Stack>
  );
}

/**
 * CSV import into one module: from a file or pasted text (an AI chat's
 * answer; a surrounding ```csv code fence is accepted). The server checks
 * everything (all-or-nothing); the import is offered only after a preview of
 * exactly the current input and mode succeeded.
 */
export function ImportCsvModal({
  opened,
  onClose,
  projectId,
  moduleId,
  moduleName,
  initialInput = 'file',
  initialMode = 'create',
}: ImportCsvModalProps) {
  const importCsv = useImportCsv(projectId, moduleId);
  const previewCsv = usePreviewCsvImport(projectId, moduleId);
  const [input, setInput] = useState<CsvInputKind>(initialInput);
  const [mode, setMode] = useState<CsvImportMode>(initialMode);
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<CsvImportPreview | null>(null);

  const busy = importCsv.isPending || previewCsv.isPending;
  const source: CsvSource | null = input === 'file' ? file : text.trim() ? text : null;

  // Any change of the input or mode invalidates the preview.
  const changed = () => {
    setPreview(null);
    setError(null);
  };

  const handleClose = () => {
    if (busy) return;
    importCsv.reset();
    previewCsv.reset();
    onClose();
  };

  const failed = (fallback: string) => (err: unknown) => {
    setPreview(null);
    setError(err instanceof ApiError ? err.message : fallback);
  };

  const handlePreview = () => {
    if (!source) return;
    setError(null);
    previewCsv.mutate({ source, mode }, { onSuccess: setPreview, onError: failed('Failed to check the CSV') });
  };

  const handleImport = () => {
    if (!source || !preview) return;
    setError(null);
    importCsv.mutate(
      { source, mode },
      {
        onSuccess: (result) => {
          const parts = [`Imported ${result.imported} new requirement(s)`];
          if (result.updated !== undefined) parts.push(`updated ${result.updated}`);
          if (result.linked !== undefined) parts.push(`created ${result.linked} link(s)`);
          const ignored = result.ignored_columns.length
            ? ` Ignored system column(s): ${result.ignored_columns.join(', ')}.`
            : '';
          notifications.show({ message: `${parts.join(', ')} in "${moduleName}".${ignored}`, color: 'green' });
          handleClose();
        },
        onError: failed('Failed to import CSV'),
      },
    );
  };

  return (
    <Modal opened={opened} onClose={handleClose} title={`Import CSV into "${moduleName}"`} centered size="xl">
      <Stack gap="sm">
        <Text size="sm">
          Use the same layout as Export CSV: a header row with <b>Id</b>, <b>Parent</b> and one column per project
          attribute (by name); <b>Module</b> is optional.
        </Text>
        <List size="sm" spacing={2}>
          <List.Item>New requirement ids are allocated by the server. The Id column is only used to match Parent cells.</List.Item>
          <List.Item>Parent must be empty (module root) or the Id of another row; in update mode also an existing requirement.</List.Item>
          <List.Item>
            Optional <b>Link: &lt;link name&gt;</b> columns link each row to existing requirements (ids separated by
            &quot;;&quot;). Link mirror columns are ignored.
          </List.Item>
          <List.Item>Unknown columns or invalid values reject the whole file; nothing is imported.</List.Item>
        </List>
        <Group gap="md" wrap="wrap">
          <SegmentedControl
            size="xs"
            value={input}
            onChange={(value) => {
              setInput(value as CsvInputKind);
              changed();
            }}
            data={[
              { value: 'file', label: 'CSV file' },
              { value: 'paste', label: 'Paste text' },
            ]}
            disabled={busy}
          />
          <SegmentedControl
            size="xs"
            value={mode}
            onChange={(value) => {
              setMode(value as CsvImportMode);
              changed();
            }}
            data={[
              { value: 'create', label: 'Create new requirements' },
              { value: 'update', label: 'Update existing (match Id)' },
            ]}
            disabled={busy}
          />
        </Group>
        {mode === 'update' && (
          <Text size="xs" c="dimmed">
            A row whose Id is a requirement of this module changes it: its non-empty cells replace the values, empty
            cells leave them unchanged. Other rows become new requirements.
          </Text>
        )}
        {input === 'file' ? (
          <FileInput
            label="CSV file"
            placeholder="Choose a .csv file"
            accept=".csv,text/csv"
            value={file}
            onChange={(value) => {
              setFile(value);
              changed();
            }}
            clearable
            disabled={busy}
          />
        ) : (
          <Textarea
            label="CSV text"
            description="Paste the AI chat's answer (a ```csv code block is fine)."
            placeholder={'Id,Parent,ObjectText\nN1,,The system shall …'}
            value={text}
            onChange={(event) => {
              setText(event.currentTarget.value);
              changed();
            }}
            autosize
            minRows={6}
            maxRows={14}
            styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }}
            disabled={busy}
            data-autofocus
          />
        )}
        {error && (
          <Alert color="red" icon={<IconAlertCircle size={16} />} title="Import failed">
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {error}
            </Text>
          </Alert>
        )}
        {preview && <PreviewSummary preview={preview} />}
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={handleClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="light"
            leftSection={<IconEye size={16} />}
            onClick={handlePreview}
            disabled={!source || importCsv.isPending}
            loading={previewCsv.isPending}
          >
            Preview
          </Button>
          <Button
            leftSection={<IconFileImport size={16} />}
            onClick={handleImport}
            disabled={!source || !preview || previewCsv.isPending}
            loading={importCsv.isPending}
          >
            Import
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
