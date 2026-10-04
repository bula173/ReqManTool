import { Alert, Button, FileInput, Group, List, Modal, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertCircle, IconFileImport } from '@tabler/icons-react';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useImportCsv } from '../api/queries';

interface ImportCsvModalProps {
  opened: boolean;
  onClose: () => void;
  projectId: string;
  moduleId: string;
  moduleName: string;
}

export function ImportCsvModal({ opened, onClose, projectId, moduleId, moduleName }: ImportCsvModalProps) {
  const importCsv = useImportCsv(projectId, moduleId);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleClose = () => {
    if (importCsv.isPending) return;
    setFile(null);
    setError(null);
    importCsv.reset();
    onClose();
  };

  const handleImport = () => {
    if (!file) return;
    setError(null);
    importCsv.mutate(file, {
      onSuccess: (result) => {
        const ignored = result.ignored_columns.length
          ? ` Ignored system column(s): ${result.ignored_columns.join(', ')}.`
          : '';
        notifications.show({
          message: `Imported ${result.imported} requirement(s) into "${moduleName}".${ignored}`,
          color: 'green',
        });
        handleClose();
      },
      onError: (err) => {
        setError(err instanceof ApiError ? err.message : 'Failed to import CSV');
      },
    });
  };

  return (
    <Modal opened={opened} onClose={handleClose} title={`Import CSV into "${moduleName}"`} centered size="lg">
      <Stack gap="sm">
        <Text size="sm">
          Use the same layout as Export CSV: a header row with <b>Id</b>, <b>Module</b>, <b>Parent</b> and one column
          per project attribute (by name). Every row becomes a new requirement in this module.
        </Text>
        <List size="sm" spacing={2}>
          <List.Item>New requirement ids are allocated by the server. The Id column is only used to match Parent cells.</List.Item>
          <List.Item>Parent must be empty (module root) or the Id of another row in the same file.</List.Item>
          <List.Item>Link columns are ignored. Unknown columns or invalid values reject the whole file.</List.Item>
        </List>
        <FileInput
          label="CSV file"
          placeholder="Choose a .csv file"
          accept=".csv,text/csv"
          value={file}
          onChange={(value) => {
            setFile(value);
            setError(null);
          }}
          clearable
          disabled={importCsv.isPending}
        />
        {error && (
          <Alert color="red" icon={<IconAlertCircle size={16} />} title="Import failed">
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {error}
            </Text>
          </Alert>
        )}
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={handleClose} disabled={importCsv.isPending}>
            Cancel
          </Button>
          <Button
            leftSection={<IconFileImport size={16} />}
            onClick={handleImport}
            disabled={!file}
            loading={importCsv.isPending}
          >
            Import
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
