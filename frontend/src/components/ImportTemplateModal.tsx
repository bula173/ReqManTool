import { Alert, Button, FileInput, Group, Modal, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertCircle, IconUpload } from '@tabler/icons-react';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useImportTemplate } from '../api/queries';

interface ImportTemplateModalProps {
  opened: boolean;
  onClose: () => void;
}

/** File ▸ Load template ▸ Import template file: uploads a template JSON file as a user template. */
export function ImportTemplateModal({ opened, onClose }: ImportTemplateModalProps) {
  const importTemplate = useImportTemplate();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleClose = () => {
    if (importTemplate.isPending) return;
    setFile(null);
    setError(null);
    importTemplate.reset();
    onClose();
  };

  const handleImport = () => {
    if (!file) return;
    setError(null);
    importTemplate.mutate(file, {
      onSuccess: (template) => {
        notifications.show({
          message: `Template "${template.name}" imported; use File › Load template to create a project from it.`,
          color: 'green',
        });
        handleClose();
      },
      onError: (err) => {
        setError(err instanceof ApiError ? err.message : 'Failed to import template');
      },
    });
  };

  return (
    <Modal opened={opened} onClose={handleClose} title="Import template file" centered size="lg">
      <Stack gap="sm">
        <Text size="sm">
          Adds a project template (a <b>.json</b> file with <b>format_version</b> 1, e.g. one saved with "Save as
          template" in another data folder) to this data folder's user templates. The file is checked like any
          template before it is saved; a template with the same name is never overwritten.
        </Text>
        <FileInput
          label="Template file"
          placeholder="Choose a .json file"
          accept=".json,application/json"
          value={file}
          onChange={(value) => {
            setFile(value);
            setError(null);
          }}
          clearable
          disabled={importTemplate.isPending}
          data-autofocus
        />
        {error && (
          <Alert color="red" icon={<IconAlertCircle size={16} />} title="Import failed">
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {error}
            </Text>
          </Alert>
        )}
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={handleClose} disabled={importTemplate.isPending}>
            Cancel
          </Button>
          <Button
            leftSection={<IconUpload size={16} />}
            onClick={handleImport}
            disabled={!file}
            loading={importTemplate.isPending}
          >
            Import
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
