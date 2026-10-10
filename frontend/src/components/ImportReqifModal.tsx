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
 * the project does not have yet as new attributes (an enumeration brings its
 * choices); attributes that already exist (same id or name) are reused and
 * their values land in them. A SPEC-RELATION-TYPE becomes a link
 * configuration and each SPEC-RELATION a link in it. Nothing of the project is
 * overwritten: existing requirements stay, and the document's requirements are
 * added alongside. The whole document is validated first, so an invalid one
 * imports nothing at all.
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
        const parts = [`${result.requirements_created} requirement(s)`];
        if (result.modules_created) parts.push(`${result.modules_created} module(s)`);
        if (result.attributes_created) parts.push(`${result.attributes_created} attribute(s)`);
        if (result.links_created) {
          parts.push(`${result.links_created} link(s) in ${result.link_configurations_created} configuration(s)`);
        }
        notifications.show({
          message: `Imported ${parts.join(', ')} into "${projectName}".`,
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
            An ATTRIBUTE-DEFINITION the project already has (same id or name) is reused; any other one is added. An
            enumeration keeps its choices.
          </List.Item>
          <List.Item>Formatted XHTML values keep their formatting and stay editable.</List.Item>
          <List.Item>Each SPEC-RELATION-TYPE becomes a link configuration, and every SPEC-RELATION a link in it.</List.Item>
          <List.Item>
            Not imported: a link configuration with no links (the document has no SPEC-RELATION for it), and the
            second value of an attribute that allows several.
          </List.Item>
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
