import { Anchor, Loader, Select, Text } from '@mantine/core';
import { useEffect } from 'react';
import { ApiError } from '../api/client';
import { useProviderConnections } from '../api/queries';
import { useAuth } from '../auth/context';

/** The pseudo connection id of the login account (`LOGIN_CONNECTION_ID` on the backend). */
export const LOGIN_CONNECTION_ID = 'login';

/**
 * Picks where a repository is created / where a remote belongs: the login
 * account of the sign-in (server edition, when its provider hosts git
 * repositories), or a stored GitLab/GitHub connection (with a token).
 * Selects the first one by itself; with none, offers the connections dialog.
 */
export function ProviderConnectionSelect({
  value,
  onChange,
  disabled,
  onManage,
  label = 'Publish with',
}: {
  value: string | null;
  onChange: (id: string | null) => void;
  disabled?: boolean;
  onManage: () => void;
  /** The Select's label ("Publish with", "Clone from", …). */
  label?: string;
}) {
  const { loginAccount } = useAuth();
  const connectionsQuery = useProviderConnections();
  const usable = (connectionsQuery.data ?? []).filter((connection) => connection.has_token);

  useEffect(() => {
    if (value !== null && (value === LOGIN_CONNECTION_ID || usable.some((connection) => connection.id === value))) {
      return;
    }
    if (!connectionsQuery.isSuccess && !loginAccount) return;
    const first = loginAccount ? LOGIN_CONNECTION_ID : (usable[0]?.id ?? null);
    if (first !== value) onChange(first);
  }, [connectionsQuery.isSuccess, usable, loginAccount, value, onChange]);

  if (connectionsQuery.isLoading && !loginAccount) return <Loader size="xs" />;
  if (connectionsQuery.isError && !loginAccount) {
    const error = connectionsQuery.error;
    return (
      <Text size="sm" c="red">
        Failed to load the connections: {error instanceof ApiError ? error.message : 'Unknown error'}
      </Text>
    );
  }
  if (usable.length === 0 && !loginAccount) {
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
      label={label}
      data={[
        // The login account: the OAuth token of this sign-in on the login
        // provider's host (server edition); never a stored connection.
        ...(loginAccount
          ? [
              {
                value: LOGIN_CONNECTION_ID,
                label: `${loginAccount.name} (your login on ${loginAccount.url.replace(/^https?:\/\//, '')})`,
              },
            ]
          : []),
        ...usable.map((connection) => ({
          value: connection.id,
          label: `${connection.name} — ${connection.username} (${connection.url})`,
        })),
      ]}
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
