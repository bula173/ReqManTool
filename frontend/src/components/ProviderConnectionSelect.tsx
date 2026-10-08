import { Anchor, Loader, Select, Text } from '@mantine/core';
import { useEffect } from 'react';
import { ApiError } from '../api/client';
import { useProviderConnections } from '../api/queries';

/**
 * Picks a GitLab/GitHub connection (only those with a stored token). Selects
 * the first one by itself; with none, offers to open the connections dialog.
 */
export function ProviderConnectionSelect({
  value,
  onChange,
  disabled,
  onManage,
}: {
  value: string | null;
  onChange: (id: string | null) => void;
  disabled?: boolean;
  onManage: () => void;
}) {
  const connectionsQuery = useProviderConnections();
  const usable = (connectionsQuery.data ?? []).filter((connection) => connection.has_token);

  useEffect(() => {
    if (!connectionsQuery.isSuccess) return;
    if (value !== null && usable.some((connection) => connection.id === value)) return;
    const first = usable[0]?.id ?? null;
    if (first !== value) onChange(first);
  }, [connectionsQuery.isSuccess, usable, value, onChange]);

  if (connectionsQuery.isLoading) return <Loader size="xs" />;
  if (connectionsQuery.isError) {
    const error = connectionsQuery.error;
    return (
      <Text size="sm" c="red">
        Failed to load the connections: {error instanceof ApiError ? error.message : 'Unknown error'}
      </Text>
    );
  }
  if (usable.length === 0) {
    return (
      <Text size="sm">
        No GitLab / GitHub connection with a token yet.{' '}
        <Anchor component="button" type="button" size="sm" onClick={onManage}>
          Add a connection…
        </Anchor>
      </Text>
    );
  }
  return (
    <Select
      label="Connection"
      data={usable.map((connection) => ({
        value: connection.id,
        label: `${connection.name} — ${connection.username} (${connection.url})`,
      }))}
      value={value}
      onChange={onChange}
      allowDeselect={false}
      disabled={disabled}
      description={
        <Anchor component="button" type="button" size="xs" onClick={onManage}>
          Manage connections…
        </Anchor>
      }
    />
  );
}
