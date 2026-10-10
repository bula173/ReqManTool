import { Alert, Button, FileInput, Group, List, Modal, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertCircle, IconFileImport } from '@tabler/icons-react';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useImportReqif } from '../api/queries';

interface ImportReqifModalProps {
  onClose: () => void;
  projectId: string;
  projectName: string;
}

/**
 * File ▸ Import ▸ ReqIF: a ReqIF document (as the ReqIF export writes one, or
 * as another tool does) becomes requirements of this project. One SPECIFICATION
 * per module, the SPEC-HIERARCHY tree as nested requirements, ATTRIBUTE-DEFINITIONs
 * the project does not have yet as new attributes; attributes that already
 * exist (same id or name) are reused and their values land in them. Nothing of
 * the project is overwritten: existing requirements stay, and the document's
 * requirements are added alongside. The whole document is validated first, so
 * an invalid one imports nothing at all.
 */
export function ImportReqifModal({ onClose, projectId, projectName }: ImportReqifModalProps) {
  const importMutation = useImportReqif(projectId);
  const [file, setFile] = useState<File | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const pending = importMutation.isPending;

  const handleImport = () => {
    if (!file) return;
    setImportError(null);
    importMutation.mutate(file, {
      onSuccess: (result) => {
        const modules = result.modules_created
          ? `, ${result.modules_created} module(s)`
          : '';
        const attributes = result.attributes_created
          ? ` and ${result.attributes_created} attribute(s)`
          : '';
        notifications.show({
          message: `Imported ${result.requirements_created} requirement(s)${modules}${attributes} into "${projectName}".`,
          color: 'green',
        });
        onClose();
      },
      onError: (err) =>
        setImportError(err instanceof ApiError ? err.message : 'Failed to import the ReqIF document'),
    });
  };

  return (
    <Modal
      opened
      onClose={() => !pending && onClose()}
      title={`Import a ReqIF document into "${projectName}"`}
      centered
      size="lg"
    >
      <Stack gap="sm">
        <Text size="sm">
          A ReqIF document (<code>.reqif</code>). It adds requirements to this project; it never changes the ones
          already there.
        </Text>
        <List size="sm" spacing={2}>
          <List.Item>Each SPECIFICATION becomes a module — or, when a module of the same name is already there, adds to it.</List.Item>
          <List.Item>The SPEC-HIERARCHY tree becomes nested requirements, with server-allocated ids.</List.Item>
          <List.Item>
            An ATTRIBUTE-DEFINITION the project already has (same id or name) is reused; any other one is added.
          </List.Item>
          <List.Item>Formatted XHTML values keep their formatting and stay editable.</List.Item>
          <List.Item>Link relations and enum value lists in the document are not imported.</List.Item>
        </List>
        <FileInput
          label="ReqIF document"
          placeholder="Choose a .reqif file"
          accept=".reqif,.xml,application/xml,text/xml"
          value={file}
          onChange={setFile}
          clearable
          disabled={pending}
        />
        {importError && (
          <Alert color="red" icon={<IconAlertCircle size={16} />} title="Import failed">
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {importError}
            </Text>
          </Alert>
        )}
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button
            leftSection={<IconFileImport size={16} />}
            onClick={handleImport}
            disabled={!file}
            loading={pending}
          >
            Import
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
