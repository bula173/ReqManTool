import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  PasswordInput,
  SegmentedControl,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconKey, IconPlus, IconTrash } from '@tabler/icons-react';
import { useState } from 'react';
import { ApiError } from '../api/client';
import {
  useAddProviderConnection,
  useProviderConnections,
  useRemoveProviderConnection,
  useUpdateProviderConnection,
} from '../api/queries';
import type { ProviderConnection, ProviderKind } from '../api/types';

const DEFAULT_URLS: Record<ProviderKind, string> = {
  gitlab: 'https://gitlab.com',
  github: 'https://github.com',
};

const TOKEN_HELP: Record<ProviderKind, string> = {
  gitlab: 'A GitLab personal access token with the “api” scope (Preferences ▸ Access tokens).',
  github:
    'A GitHub personal access token: classic with the “repo” (and “read:org”) scope, or fine-grained with ' +
    'Contents, Pull requests and Administration (create repositories) access.',
};

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}

/** The form that adds a connection; the token goes to the server once and is stored in its OS keychain. */
function AddConnectionForm({ onAdded, onCancel }: { onAdded: () => void; onCancel: () => void }) {
  const add = useAddProviderConnection();
  const [provider, setProvider] = useState<ProviderKind>('gitlab');
  const [url, setUrl] = useState('');
  const [name, setName] = useState('');
  const [token, setToken] = useState('');

  const submit = () => {
    add.mutate(
      { provider, url: url.trim(), name: name.trim(), token: token.trim() },
      {
        onSuccess: (connection) => {
          notifications.show({
            message: `Connected to ${connection.url} as ${connection.username}`,
            color: 'green',
          });
          setToken('');
          onAdded();
        },
      },
    );
  };

  return (
    <Stack gap="xs">
      <Title order={5}>Add a connection</Title>
      <SegmentedControl
        value={provider}
        onChange={(value) => setProvider(value as ProviderKind)}
        data={[
          { value: 'gitlab', label: 'GitLab' },
          { value: 'github', label: 'GitHub' },
        ]}
        disabled={add.isPending}
      />
      <TextInput
        label="Server URL"
        description={
          provider === 'gitlab'
            ? 'Empty for GitLab.com, or your self-hosted GitLab (https://gitlab.example.com)'
            : 'Empty for GitHub.com, or your GitHub Enterprise server (https://github.example.com)'
        }
        placeholder={DEFAULT_URLS[provider]}
        value={url}
        onChange={(event) => setUrl(event.currentTarget.value)}
        disabled={add.isPending}
      />
      <TextInput
        label="Name"
        description="Optional; shown in pickers"
        placeholder={provider === 'gitlab' ? 'Company GitLab' : 'GitHub'}
        value={name}
        onChange={(event) => setName(event.currentTarget.value)}
        disabled={add.isPending}
      />
      <PasswordInput
        label="Personal access token"
        description={TOKEN_HELP[provider]}
        required
        value={token}
        onChange={(event) => setToken(event.currentTarget.value)}
        disabled={add.isPending}
        autoComplete="off"
      />
      <Text size="xs" c="dimmed">
        The token is checked with the server, then stored in this computer’s keychain by the ReqManTool backend. It
        is never written to project data, git or config files, and never shown again.
      </Text>
      {add.isError && (
        <Text size="sm" c="red" style={{ wordBreak: 'break-word' }}>
          {errorText(add.error, 'Failed to add the connection')}
        </Text>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={onCancel} disabled={add.isPending}>
          Cancel
        </Button>
        <Button onClick={submit} loading={add.isPending} disabled={token.trim().length === 0}>
          Connect
        </Button>
      </Group>
    </Stack>
  );
}

/** One row: name, server, user, token state; replace token, remove. */
function ConnectionRow({ connection }: { connection: ProviderConnection }) {
  const update = useUpdateProviderConnection();
  const remove = useRemoveProviderConnection();
  const [replacing, setReplacing] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [token, setToken] = useState('');
  const busy = update.isPending || remove.isPending;

  return (
    <Table.Tr>
      <Table.Td>
        <Stack gap={2}>
          <Group gap={6} wrap="nowrap">
            <Badge size="xs" variant="light" color={connection.provider === 'gitlab' ? 'orange' : 'dark'}>
              {connection.provider_label}
            </Badge>
            <Text size="sm" fw={500}>
              {connection.name}
            </Text>
          </Group>
          <Text size="xs" c="dimmed" ff="monospace">
            {connection.url}
          </Text>
        </Stack>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{connection.username || '—'}</Text>
      </Table.Td>
      <Table.Td>
        {connection.has_token === true && (
          <Badge size="xs" color="green" variant="light">
            token stored
          </Badge>
        )}
        {connection.has_token === false && (
          <Badge size="xs" color="red" variant="light">
            no token
          </Badge>
        )}
        {connection.has_token === null && (
          <Text size="xs" c="red">
            {connection.token_error ?? 'Keychain unavailable'}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        {replacing ? (
          <Stack gap={4}>
            <PasswordInput
              size="xs"
              placeholder="New personal access token"
              value={token}
              onChange={(event) => setToken(event.currentTarget.value)}
              disabled={busy}
              autoComplete="off"
            />
            <Group gap={4} justify="flex-end" wrap="nowrap">
              <Button size="compact-xs" variant="default" disabled={busy} onClick={() => setReplacing(false)}>
                Cancel
              </Button>
              <Button
                size="compact-xs"
                loading={update.isPending}
                disabled={token.trim().length === 0}
                onClick={() =>
                  update.mutate(
                    { id: connection.id, token: token.trim() },
                    {
                      onSuccess: () => {
                        setReplacing(false);
                        setToken('');
                        notifications.show({ message: 'Token replaced', color: 'green' });
                      },
                    },
                  )
                }
              >
                Save
              </Button>
            </Group>
            {update.isError && (
              <Text size="xs" c="red" style={{ wordBreak: 'break-word' }}>
                {errorText(update.error, 'Failed to replace the token')}
              </Text>
            )}
          </Stack>
        ) : confirmRemove ? (
          <Group gap={4} justify="flex-end" wrap="nowrap">
            <Text size="xs">Remove?</Text>
            <Button size="compact-xs" variant="default" disabled={busy} onClick={() => setConfirmRemove(false)}>
              No
            </Button>
            <Button
              size="compact-xs"
              color="red"
              loading={remove.isPending}
              onClick={() =>
                remove.mutate(connection.id, {
                  onError: (error) =>
                    notifications.show({ message: errorText(error, 'Failed to remove the connection'), color: 'red' }),
                })
              }
            >
              Remove
            </Button>
          </Group>
        ) : (
          <Group gap={4} justify="flex-end" wrap="nowrap">
            <Button
              size="compact-xs"
              variant="default"
              leftSection={<IconKey size={12} />}
              disabled={busy}
              onClick={() => setReplacing(true)}
            >
              Replace token
            </Button>
            <Button
              size="compact-xs"
              variant="subtle"
              color="red"
              leftSection={<IconTrash size={12} />}
              disabled={busy}
              onClick={() => setConfirmRemove(true)}
            >
              Remove
            </Button>
          </Group>
        )}
      </Table.Td>
    </Table.Tr>
  );
}

/**
 * Settings ▸ GitLab / GitHub connections: provider connections used by
 * “Publish project”, “Add project from GitLab/GitHub” and reviews. Tokens are
 * only ever sent to the server (stored in its OS keychain), never read back.
 */
export function ProviderConnectionsModal({ onClose }: { onClose: () => void }) {
  const connectionsQuery = useProviderConnections();
  const [adding, setAdding] = useState(false);
  const connections = connectionsQuery.data ?? [];
  const showForm = adding || (connectionsQuery.isSuccess && connections.length === 0);

  return (
    <Modal opened onClose={onClose} title="GitLab / GitHub connections" size="xl" centered>
      <Stack gap="md">
        {connectionsQuery.isLoading && <Loader size="sm" />}
        {connectionsQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            Failed to load the connections: {errorText(connectionsQuery.error, 'Unknown error')}
          </Alert>
        )}
        {connectionsQuery.isSuccess && connections.length === 0 && (
          <Text size="sm" c="dimmed">
            No connections yet. A connection lets ReqManTool create repositories, list your repositories and open
            merge / pull requests on GitLab or GitHub for you.
          </Text>
        )}
        {connections.length > 0 && (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Connection</Table.Th>
                <Table.Th>User</Table.Th>
                <Table.Th>Token</Table.Th>
                <Table.Th w={260} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {connections.map((connection) => (
                <ConnectionRow key={connection.id} connection={connection} />
              ))}
            </Table.Tbody>
          </Table>
        )}
        {showForm ? (
          <AddConnectionForm
            onAdded={() => setAdding(false)}
            onCancel={() => (adding ? setAdding(false) : onClose())}
          />
        ) : (
          connectionsQuery.isSuccess && (
            <Group justify="space-between">
              <Button leftSection={<IconPlus size={14} />} variant="default" onClick={() => setAdding(true)}>
                Add connection
              </Button>
              <Button onClick={onClose}>Close</Button>
            </Group>
          )
        )}
      </Stack>
    </Modal>
  );
}
