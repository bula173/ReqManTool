import { Button, Group, Modal, Stack, Text, TextInput } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useCreateView } from '../api/queries';
import type { View, ViewCreate } from '../api/types';
import { clearViewLayoutDraft } from '../hooks/columnLayout';

interface SaveLayoutAsViewModalProps {
  projectId: string;
  layout: Pick<ViewCreate, 'attribute_ids' | 'column_widths' | 'single_line'>;
  /** The named view (and table key) the layout came from; its unsaved draft is dropped once saved. */
  source: { tableKey: string; view: View } | null;
  /** Called with the new view (e.g. to make it the active view). */
  onCreated: (view: View) => void;
  onClose: () => void;
}

/** "Save current layout as new view": a name for a new view holding the open table's columns, widths and wrap. */
export function SaveLayoutAsViewModal({ projectId, layout, source, onCreated, onClose }: SaveLayoutAsViewModalProps) {
  const createView = useCreateView(projectId);
  const [name, setName] = useState(source ? `${source.view.name} (copy)` : '');
  const [error, setError] = useState<string | null>(null);
  const columns = layout.attribute_ids.length;

  const submit = () => {
    if (name.trim().length === 0) {
      setError('Name is required');
      return;
    }
    createView.mutate(
      { name, ...layout },
      {
        onSuccess: (view) => {
          // The source view stays as saved; its unsaved changes now live in the new view.
          if (source) clearViewLayoutDraft(source.tableKey, source.view);
          notifications.show({ message: `View "${view.name}" created`, color: 'green' });
          onCreated(view);
          onClose();
        },
        onError: (err) => setError(err instanceof ApiError ? err.message : 'Failed to create the view'),
      },
    );
  };

  return (
    <Modal opened onClose={onClose} title="Save current layout as new view" centered>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            The new view stores the table's {columns} attribute column{columns === 1 ? '' : 's'} in their current
            order, with their widths and wrap settings, in the project (shared with everyone who opens it).
          </Text>
          <TextInput
            label="Name"
            required
            data-autofocus
            value={name}
            error={error}
            onChange={(e) => {
              setName(e.currentTarget.value);
              setError(null);
            }}
          />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={createView.isPending}>
              Create view
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
