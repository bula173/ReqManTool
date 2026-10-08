import { Anchor, Button, Group, Modal, Select, Stack, Switch, Text, TextInput, Textarea } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useCallback, useState } from 'react';
import { ApiError } from '../api/client';
import { useProviderNamespaces, usePublishProject } from '../api/queries';
import { ProviderConnectionSelect } from './ProviderConnectionSelect';

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}

/**
 * “Publish project”: create a repository on GitLab/GitHub (namespace / group
 * / organisation, name, private by default), set it as the project's remote
 * and push. All provider calls run on the server with the connection's
 * keychain token; the remote URL stored in git carries no credentials.
 */
export function PublishProjectModal({
  projectId,
  projectName,
  projectDescription,
  onClose,
  onManageConnections,
}: {
  projectId: string;
  projectName: string;
  projectDescription: string;
  onClose: () => void;
  onManageConnections: () => void;
}) {
  const publish = usePublishProject(projectId);
  const [connectionId, setConnectionId] = useState<string | null>(null);
  const [namespaceId, setNamespaceId] = useState<string | null>(null);
  const [name, setName] = useState(projectName);
  const [isPrivate, setIsPrivate] = useState(true);
  const [description, setDescription] = useState(projectDescription);
  const namespacesQuery = useProviderNamespaces(connectionId);
  const namespaces = namespacesQuery.data;

  const selectConnection = useCallback((id: string | null) => {
    setConnectionId(id);
    setNamespaceId(null);
  }, []);

  // The user's own namespace (listed first) until another one is chosen.
  const ownerId = namespaceId ?? namespaces?.[0]?.id ?? null;

  const submit = () => {
    if (!connectionId) return;
    publish.mutate(
      { connection_id: connectionId, namespace_id: ownerId, name: name.trim(), private: isPrivate, description },
      {
        onSuccess: (result) => {
          notifications.show({
            message: `Published to ${result.repository.web_url ?? result.repository.full_path}`,
            color: 'green',
          });
          onClose();
        },
      },
    );
  };

  return (
    <Modal opened onClose={onClose} title="Publish project to GitLab / GitHub" size="lg" centered>
      <Stack gap="sm">
        <ProviderConnectionSelect
          value={connectionId}
          onChange={selectConnection}
          disabled={publish.isPending}
          onManage={onManageConnections}
        />
        {connectionId && (
          <>
            {namespacesQuery.isError ? (
              <Text size="sm" c="red" style={{ wordBreak: 'break-word' }}>
                Failed to load the groups / organisations: {errorText(namespacesQuery.error, 'Unknown error')}
              </Text>
            ) : (
              <Select
                label="Owner"
                description="Your own namespace, or a group / organisation you may create repositories in"
                data={(namespaces ?? []).map((ns) => ({
                  value: ns.id,
                  label: ns.kind === 'user' ? `${ns.path} (you)` : ns.path,
                }))}
                value={ownerId}
                onChange={setNamespaceId}
                allowDeselect={false}
                searchable
                disabled={publish.isPending || namespacesQuery.isLoading}
                placeholder={namespacesQuery.isLoading ? 'Loading…' : undefined}
              />
            )}
            <TextInput
              label="Repository name"
              required
              value={name}
              onChange={(event) => setName(event.currentTarget.value)}
              disabled={publish.isPending}
            />
            <Textarea
              label="Description"
              autosize
              minRows={1}
              maxRows={4}
              value={description}
              onChange={(event) => setDescription(event.currentTarget.value)}
              disabled={publish.isPending}
            />
            <Switch
              label="Private repository"
              description="Recommended: only members you add can see the requirements"
              checked={isPrivate}
              onChange={(event) => setIsPrivate(event.currentTarget.checked)}
              disabled={publish.isPending}
            />
          </>
        )}
        <Text size="xs" c="dimmed">
          The repository is created empty, set as this project’s remote and the project is pushed (branch “current”,
          the baseline branch and baseline tags). Push and Pull then authenticate with the connection’s token.
        </Text>
        {publish.isError && (
          <Text size="sm" c="red" style={{ wordBreak: 'break-word' }}>
            {errorText(publish.error, 'Publishing failed')}
          </Text>
        )}
        {publish.isError && publish.error instanceof ApiError && publish.error.message.includes('was created') && (
          <Text size="xs">
            The remote may already be configured: check the Git panel, fix the cause and use Push.{' '}
            <Anchor component="button" type="button" size="xs" onClick={onClose}>
              Close
            </Anchor>
          </Text>
        )}
        <Group justify="flex-end" mt="xs">
          <Button variant="default" onClick={onClose} disabled={publish.isPending}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            loading={publish.isPending}
            disabled={!connectionId || !ownerId || name.trim().length === 0}
          >
            Create and push
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
