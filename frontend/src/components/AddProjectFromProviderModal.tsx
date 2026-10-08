import { Badge, Button, Group, Loader, Modal, Radio, ScrollArea, Stack, Text, TextInput } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconSearch } from '@tabler/icons-react';
import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useAddProjectFromProvider, useProviderRepositories } from '../api/queries';
import { ProviderConnectionSelect } from './ProviderConnectionSelect';

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError || error instanceof Error ? error.message : fallback;
}

/**
 * “Add project from GitLab/GitHub”: browse or search the user's repositories
 * on a connection and clone the chosen one into the data directory (the
 * same path as “Add project from remote”; the server looks the clone URL up).
 */
export function AddProjectFromProviderModal({
  onClose,
  onManageConnections,
}: {
  onClose: () => void;
  onManageConnections: () => void;
}) {
  const navigate = useNavigate();
  const addProject = useAddProjectFromProvider();
  const [connectionId, setConnectionId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [debouncedSearch] = useDebouncedValue(search.trim(), 400);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const reposQuery = useProviderRepositories(connectionId, debouncedSearch, page);
  const repos = reposQuery.data;

  const selectConnection = useCallback((id: string | null) => {
    setConnectionId(id);
    setPage(1);
    setSelected(null);
  }, []);

  const submit = () => {
    if (!connectionId || !selected) return;
    addProject.mutate(
      { connection_id: connectionId, repository: selected },
      {
        onSuccess: (project) => {
          notifications.show({ message: `Project "${project.name}" added from ${selected}`, color: 'green' });
          onClose();
          navigate(`/projects/${project.id}`);
        },
      },
    );
  };

  return (
    <Modal opened onClose={onClose} title="Add project from GitLab / GitHub" size="lg" centered>
      <Stack gap="sm">
        <ProviderConnectionSelect
          value={connectionId}
          onChange={selectConnection}
          disabled={addProject.isPending}
          onManage={onManageConnections}
        />
        {connectionId && (
          <>
            <TextInput
              leftSection={<IconSearch size={14} />}
              placeholder="Search your repositories by name"
              value={search}
              onChange={(event) => {
                setSearch(event.currentTarget.value);
                setPage(1);
              }}
              disabled={addProject.isPending}
            />
            {reposQuery.isLoading && <Loader size="xs" />}
            {reposQuery.isError && (
              <Text size="sm" c="red" style={{ wordBreak: 'break-word' }}>
                Failed to list the repositories: {errorText(reposQuery.error, 'Unknown error')}
              </Text>
            )}
            {repos && repos.repositories.length === 0 && (
              <Text size="sm" c="dimmed">
                {debouncedSearch ? 'No repository matches the search.' : 'No repositories found.'}
              </Text>
            )}
            {repos && repos.repositories.length > 0 && (
              <ScrollArea.Autosize mah={320}>
                <Radio.Group value={selected} onChange={setSelected}>
                  <Stack gap={6}>
                    {repos.repositories.map((repo) => (
                      <Radio
                        key={repo.full_path}
                        value={repo.full_path}
                        disabled={addProject.isPending}
                        label={
                          <Group gap={6} wrap="nowrap">
                            <Text size="sm" ff="monospace">
                              {repo.full_path}
                            </Text>
                            {repo.private && (
                              <Badge size="xs" variant="outline" color="gray">
                                private
                              </Badge>
                            )}
                          </Group>
                        }
                        description={repo.description || undefined}
                      />
                    ))}
                  </Stack>
                </Radio.Group>
              </ScrollArea.Autosize>
            )}
            {repos?.note && (
              <Text size="xs" c="dimmed">
                {repos.note}
              </Text>
            )}
            {repos && (page > 1 || repos.has_more) && (
              <Group justify="center" gap="xs">
                <Button
                  size="compact-xs"
                  variant="default"
                  disabled={page <= 1 || reposQuery.isFetching}
                  onClick={() => setPage(page - 1)}
                >
                  Previous
                </Button>
                <Text size="xs">Page {page}</Text>
                <Button
                  size="compact-xs"
                  variant="default"
                  disabled={!repos.has_more || reposQuery.isFetching}
                  onClick={() => setPage(page + 1)}
                >
                  Next
                </Button>
              </Group>
            )}
          </>
        )}
        <Text size="xs" c="dimmed">
          The repository must hold a ReqManTool project (branch “current”). It is cloned into the data directory with
          its remote configured, so Push and Pull work right away.
        </Text>
        {addProject.isError && (
          <Text size="sm" c="red" style={{ wordBreak: 'break-word' }}>
            {errorText(addProject.error, 'Failed to add the project')}
          </Text>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={addProject.isPending}>
            Cancel
          </Button>
          <Button onClick={submit} loading={addProject.isPending} disabled={!connectionId || !selected}>
            Add project
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
