import { Alert, Button, Group, Loader, Modal, Select, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useModuleTree, useMoveModule, useMoveModuleFolder } from '../api/queries';
import { flattenFolders, folderPickerOptions, folderSubtreeIds, parentFolderOf, TOP_LEVEL_FOLDER } from './moduleTree';

interface MoveToFolderModalProps {
  projectId: string;
  item: { kind: 'module' | 'folder'; id: string; name: string };
  onClose: () => void;
}

/**
 * Move a module, or a module folder with everything in it, into another
 * folder (or the top level); it is appended to the destination's entries. A
 * folder cannot be moved into itself or one of its subfolders (left out of
 * the choices; the server refuses it too). Module ids, requirements and links
 * are not affected.
 */
export function MoveToFolderModal({ projectId, item, onClose }: MoveToFolderModalProps) {
  const treeQuery = useModuleTree(projectId);
  const moveModule = useMoveModule();
  const moveFolder = useMoveModuleFolder();
  const tree = treeQuery.data;
  const current = parentFolderOf(tree, item.kind, item.id);
  const [choice, setChoice] = useState<string | null>(null);
  const currentValue = current === undefined ? null : (current ?? TOP_LEVEL_FOLDER);
  const value = choice ?? currentValue;

  const movedFolder =
    item.kind === 'folder' ? flattenFolders(tree).find(({ folder }) => folder.folder === item.id)?.folder : undefined;
  const options = folderPickerOptions(tree, movedFolder ? folderSubtreeIds(movedFolder) : new Set()).map((option) => ({
    ...option,
    label: option.value === currentValue ? `${option.label} (current)` : option.label,
  }));
  const pending = moveModule.isPending || moveFolder.isPending;

  const submit = () => {
    if (value === null || value === currentValue) {
      onClose();
      return;
    }
    const destination = value === TOP_LEVEL_FOLDER ? null : value;
    const onSuccess = () => {
      const where = destination === null ? 'the top level' : `"${options.find((o) => o.value === value)?.label.trim()}"`;
      notifications.show({ message: `Moved "${item.name}" to ${where}`, color: 'green' });
      onClose();
    };
    const onError = (error: unknown) =>
      notifications.show({ message: error instanceof ApiError ? error.message : 'Failed to move', color: 'red' });
    if (item.kind === 'module') {
      moveModule.mutate({ projectId, moduleId: item.id, folderId: destination }, { onSuccess, onError });
    } else {
      moveFolder.mutate({ projectId, folderId: item.id, parentId: destination }, { onSuccess, onError });
    }
  };

  return (
    <Modal
      opened
      onClose={onClose}
      title={item.kind === 'module' ? `Move module "${item.name}"` : `Move folder "${item.name}"`}
      centered
    >
      <Stack gap="sm">
        {treeQuery.isLoading && <Loader size="sm" />}
        {treeQuery.isError && (
          <Alert color="red" variant="light">
            Failed to load the module folders
            {treeQuery.error instanceof Error ? `: ${treeQuery.error.message}` : ''}
          </Alert>
        )}
        {tree && (
          <>
            <Select
              label="Destination folder"
              description="It is placed at the end of the folder. Module ids, requirements and links stay unchanged."
              data={options}
              value={value}
              onChange={setChoice}
              allowDeselect={false}
              searchable
              data-autofocus
            />
            {options.length === 1 && (
              <Text size="xs" c="dimmed">
                This project has no {item.kind === 'folder' ? 'other ' : ''}folders yet: create one with "New folder".
              </Text>
            )}
          </>
        )}
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={pending} disabled={!tree || value === null || value === currentValue}>
            Move
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
