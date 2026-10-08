import { Avatar, Badge, Button, Menu, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconLogout, IconUsers } from '@tabler/icons-react';
import { useState } from 'react';
import { useAppActions } from '../actions/context';
import { useAuth } from '../auth/context';
import { AdminModal } from './AdminModal';

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : (parts[0] ?? '?').slice(0, 2)).toUpperCase();
}

/**
 * Header menu of the logged-in user (server edition): name, e-mail, the
 * Administration dialog (server admins and project admins), Log out.
 * Nothing in the local edition.
 */
export function UserMenu() {
  const { mode, user, access, logout } = useAuth();
  const { state } = useAppActions();
  const [loggingOut, setLoggingOut] = useState(false);
  const [adminOpened, setAdminOpened] = useState(false);
  if (mode !== 'server' || !user) return null;

  const onLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
    } catch (error) {
      setLoggingOut(false);
      notifications.show({ color: 'red', title: 'Log out failed', message: (error as Error).message });
    }
  };

  return (
    <>
      <Menu position="bottom-end" withinPortal>
        <Menu.Target>
          <Button
            variant="default"
            size="sm"
            leftSection={
              <Avatar size={22} radius="xl" color="blue">
                {initials(user.name || user.username || user.email)}
              </Avatar>
            }
            aria-label={`User menu: ${user.name}`}
          >
            <Text size="sm" truncate maw={160}>
              {user.name || user.username}
            </Text>
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Label>
            <Text size="sm" fw={500} c="var(--mantine-color-text)">
              {user.name}
            </Text>
            {user.email && <Text size="xs">{user.email}</Text>}
            {user.server_admin && (
              <Badge size="xs" variant="light" mt={4}>
                Server admin
              </Badge>
            )}
          </Menu.Label>
          <Menu.Divider />
          {access.canOpenAdministration && (
            <Menu.Item leftSection={<IconUsers size={16} />} onClick={() => setAdminOpened(true)}>
              Administration…
            </Menu.Item>
          )}
          <Menu.Item leftSection={<IconLogout size={16} />} onClick={onLogout} disabled={loggingOut}>
            {loggingOut ? 'Logging out…' : 'Log out'}
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      <AdminModal
        opened={adminOpened}
        onClose={() => setAdminOpened(false)}
        initialProjectId={state.projectId && access.canAdmin(state.projectId) ? state.projectId : null}
      />
    </>
  );
}
