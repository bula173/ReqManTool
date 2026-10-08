import { Button, Group, Modal, Stack, Text, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { ApiError } from '../api/client';
import { useCreateModuleFolder, useModuleTree, useRenameModuleFolder } from '../api/queries';
import { flattenFolders } from './moduleTree';

interface ModuleFolderNameModalProps {
  projectId: string;
  /** Create: the folder to create the new folder in (null: the top level). */
  parentId?: string | null;
  /** Rename this folder instead of creating one. */
  rename?: { folderId: string; name: string };
  onClose: () => void;
}

/**
 * New module folder / Rename module folder. The server allocates the id and
 * checks the name (not empty, unique among the sibling folders, length);
 * its message is shown when it refuses.
 */
export function ModuleFolderNameModal({ projectId, parentId = null, rename, onClose }: ModuleFolderNameModalProps) {
  const createFolder = useCreateModuleFolder();
  const renameFolder = useRenameModuleFolder();
  const treeQuery = useModuleTree(projectId);
  const parentPath = parentId ? flattenFolders(treeQuery.data).find(({ folder }) => folder.folder === parentId)?.path : null;
  const form = useForm<{ name: string }>({
    initialValues: { name: rename?.name ?? '' },
    validate: { name: (value) => (value.trim().length === 0 ? 'Folder name is required' : null) },
  });
  const pending = createFolder.isPending || renameFolder.isPending;

  const handleSubmit = form.onSubmit(({ name }) => {
    const onError = (error: unknown) =>
      notifications.show({
        message: error instanceof ApiError ? error.message : rename ? 'Failed to rename folder' : 'Failed to create folder',
        color: 'red',
      });
    if (rename) {
      if (name.trim() === rename.name) {
        onClose();
        return;
      }
      renameFolder.mutate(
        { projectId, folderId: rename.folderId, name: name.trim() },
        {
          onSuccess: (folder) => {
            notifications.show({ message: `Folder renamed to "${folder.name}"`, color: 'green' });
            onClose();
          },
          onError,
        },
      );
      return;
    }
    createFolder.mutate(
      { projectId, name: name.trim(), parentId },
      {
        onSuccess: (folder) => {
          notifications.show({ message: `Folder "${folder.name}" created`, color: 'green' });
          onClose();
        },
        onError,
      },
    );
  });

  return (
    <Modal opened onClose={onClose} title={rename ? 'Rename folder' : 'New folder'} centered>
      <form onSubmit={handleSubmit}>
        <Stack gap="sm">
          {!rename && (
            <Text size="sm" c="dimmed">
              {parentPath ? `In "${parentPath}".` : 'At the top level of the project.'} Folders only organise the
              module list: module ids, requirements and links are not affected.
            </Text>
          )}
          <TextInput label="Name" placeholder="Requirements" required data-autofocus {...form.getInputProps('name')} />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={pending}>
              {rename ? 'Rename' : 'Create'}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
