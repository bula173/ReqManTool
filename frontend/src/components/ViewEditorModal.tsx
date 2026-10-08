import {
  ActionIcon,
  Alert,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  Stack,
  Table,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconArrowDown, IconArrowUp, IconTrash } from '@tabler/icons-react';
import { useState } from 'react';
import { ApiError } from '../api/client';
import { useModules, useProject, useRemoveView, useUpdateView, useViews } from '../api/queries';
import type { AttributeDefinition, View } from '../api/types';
import { ID_COLUMN } from '../hooks/columnLayout';
import { ModuleTypeIcon } from './ModuleTypeIcon';

interface ViewEditorModalProps {
  projectId: string;
  viewId: string;
  onClose: () => void;
}

/**
 * Edit a named view (a project element in project.json): its name, its
 * columns (which attributes, in which order — the table's column order),
 * per-column wrap, the modules it is offered on, or delete it. Saves with
 * `PUT /projects/{id}/views/{view_id}` (shared widths are kept; widths of
 * removed columns are dropped by the server).
 */
export function ViewEditorModal({ projectId, viewId, onClose }: ViewEditorModalProps) {
  const projectQuery = useProject(projectId);
  const viewsQuery = useViews(projectId);
  const view = viewsQuery.data?.find((v) => v.id === viewId);
  const loading = projectQuery.isLoading || viewsQuery.isLoading;
  const error = projectQuery.error ?? viewsQuery.error;

  return (
    <Modal opened onClose={onClose} title={view ? `Edit view "${view.name}"` : 'Edit view'} size="lg" centered>
      {loading && <Loader size="sm" />}
      {!loading && error && (
        <Alert color="red" variant="light">
          {error instanceof Error ? error.message : 'Failed to load the view'}
        </Alert>
      )}
      {!loading && !error && !view && (
        <Alert color="orange" variant="light">
          This view no longer exists (it may have been deleted in another window).
        </Alert>
      )}
      {view && projectQuery.data && (
        <ViewEditorForm
          projectId={projectId}
          view={view}
          attributes={projectQuery.data.attributes}
          onClose={onClose}
        />
      )}
    </Modal>
  );
}

function ViewEditorForm({
  projectId,
  view,
  attributes,
  onClose,
}: {
  projectId: string;
  view: View;
  attributes: AttributeDefinition[];
  onClose: () => void;
}) {
  const updateView = useUpdateView(projectId);
  const removeView = useRemoveView(projectId);
  const modulesQuery = useModules(projectId);
  const known = new Set(attributes.map((a) => a.id));
  const [name, setName] = useState(view.name);
  // Ordered columns (attributes the project still defines), then wrap and module scope.
  const [columns, setColumns] = useState<string[]>(view.attribute_ids.filter((id) => known.has(id)));
  const [singleLine, setSingleLine] = useState<string[]>(view.single_line ?? []);
  const [modules, setModules] = useState<string[]>(view.modules ?? []);
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const attributeName = (id: string) => attributes.find((a) => a.id === id)?.name ?? id;
  const unused = attributes.filter((a) => !columns.includes(a.id));
  const busy = updateView.isPending || removeView.isPending;

  const move = (index: number, delta: number) =>
    setColumns((current) => {
      const next = [...current];
      const [item] = next.splice(index, 1);
      next.splice(index + delta, 0, item);
      return next;
    });
  const toggleWrap = (key: string, wrap: boolean) =>
    setSingleLine((current) => (wrap ? current.filter((k) => k !== key) : [...current, key]));

  const save = () => {
    if (name.trim().length === 0) {
      setFormError('Name is required');
      return;
    }
    const shown = new Set([ID_COLUMN, ...columns]);
    updateView.mutate(
      {
        viewId: view.id,
        changes: {
          name,
          attribute_ids: columns,
          single_line: singleLine.filter((k) => shown.has(k)),
          modules,
        },
      },
      {
        onSuccess: (saved) => {
          notifications.show({ message: `View "${saved.name}" saved`, color: 'green' });
          onClose();
        },
        onError: (err) => setFormError(err instanceof ApiError ? err.message : 'Failed to save the view'),
      },
    );
  };

  const remove = () =>
    removeView.mutate(view.id, {
      onSuccess: () => {
        notifications.show({ message: `View "${view.name}" deleted`, color: 'green' });
        onClose();
      },
      onError: (err) => setFormError(err instanceof ApiError ? err.message : 'Failed to delete the view'),
    });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <Stack gap="sm">
        <TextInput
          label="Name"
          required
          value={name}
          onChange={(e) => {
            setName(e.currentTarget.value);
            setFormError(null);
          }}
        />

        <div>
          <Text size="sm" fw={500}>
            Columns
          </Text>
          <Text size="xs" c="dimmed" mb={4}>
            In table order; the Id column always comes first. Untick Wrap to show a column on one line.
          </Text>
          <Table withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Column</Table.Th>
                <Table.Th w={70}>Wrap</Table.Th>
                <Table.Th w={110} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              <Table.Tr>
                <Table.Td>
                  <Text size="sm" c="dimmed">
                    Id
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Checkbox
                    aria-label="Wrap Id"
                    checked={!singleLine.includes(ID_COLUMN)}
                    onChange={(e) => toggleWrap(ID_COLUMN, e.currentTarget.checked)}
                  />
                </Table.Td>
                <Table.Td />
              </Table.Tr>
              {columns.map((id, index) => (
                <Table.Tr key={id}>
                  <Table.Td>{attributeName(id)}</Table.Td>
                  <Table.Td>
                    <Checkbox
                      aria-label={`Wrap ${attributeName(id)}`}
                      checked={!singleLine.includes(id)}
                      onChange={(e) => toggleWrap(id, e.currentTarget.checked)}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Group gap={2} wrap="nowrap">
                      <Tooltip label="Move up">
                        <ActionIcon
                          variant="subtle"
                          aria-label={`Move ${attributeName(id)} up`}
                          disabled={index === 0}
                          onClick={() => move(index, -1)}
                        >
                          <IconArrowUp size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Move down">
                        <ActionIcon
                          variant="subtle"
                          aria-label={`Move ${attributeName(id)} down`}
                          disabled={index === columns.length - 1}
                          onClick={() => move(index, 1)}
                        >
                          <IconArrowDown size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Remove column">
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          aria-label={`Remove ${attributeName(id)}`}
                          onClick={() => setColumns((current) => current.filter((c) => c !== id))}
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {unused.length > 0 && (
            <>
              <Text size="sm" fw={500} mt="xs">
                Add columns
              </Text>
              <Group gap="md" mt={4}>
                {unused.map((attr) => (
                  <Checkbox
                    key={attr.id}
                    label={attr.name}
                    checked={false}
                    onChange={() => setColumns((current) => [...current, attr.id])}
                  />
                ))}
              </Group>
            </>
          )}
        </div>

        <div>
          <Text size="sm" fw={500}>
            Applies to modules
          </Text>
          {modulesQuery.isLoading && <Loader size="xs" />}
          {modulesQuery.isError && (
            <Text size="xs" c="red">
              Failed to load modules
            </Text>
          )}
          {modulesQuery.isSuccess && modulesQuery.data.length === 0 && (
            <Text size="xs" c="dimmed">
              The project has no modules yet.
            </Text>
          )}
          {(modulesQuery.data ?? []).length > 0 && (
            <Checkbox.Group value={modules} onChange={setModules}>
              <Stack gap={4} mt={4}>
                {(modulesQuery.data ?? []).map((module) => (
                  <Checkbox
                    key={module.id}
                    value={module.id}
                    label={
                      <Group gap={4} wrap="nowrap">
                        <ModuleTypeIcon module={module} size={14} />
                        {module.name}
                      </Group>
                    }
                  />
                ))}
              </Stack>
            </Checkbox.Group>
          )}
          <Text size="xs" c="dimmed" mt={4}>
            {modules.length === 0
              ? 'None ticked: offered on every module.'
              : 'Offered only on the ticked modules (the view selector of other modules does not list it).'}
          </Text>
        </div>

        {formError && (
          <Alert color="red" variant="light" p="xs">
            {formError}
          </Alert>
        )}

        {confirmDelete ? (
          <Alert color="red" variant="light" p="xs">
            <Group justify="space-between" wrap="nowrap">
              <Text size="sm">Delete the view "{view.name}" for everyone? This cannot be undone here.</Text>
              <Group gap="xs" wrap="nowrap">
                <Button size="xs" variant="default" onClick={() => setConfirmDelete(false)} disabled={busy}>
                  Cancel
                </Button>
                <Button size="xs" color="red" onClick={remove} loading={removeView.isPending}>
                  Delete
                </Button>
              </Group>
            </Group>
          </Alert>
        ) : null}

        <Group justify="space-between" mt="sm">
          <Button
            variant="subtle"
            color="red"
            leftSection={<IconTrash size={16} />}
            onClick={() => setConfirmDelete(true)}
            disabled={busy || confirmDelete}
          >
            Delete view
          </Button>
          <Group gap="xs">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={updateView.isPending} disabled={removeView.isPending}>
              Save
            </Button>
          </Group>
        </Group>
      </Stack>
    </form>
  );
}
