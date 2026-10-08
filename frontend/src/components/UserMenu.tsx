import { Avatar, Badge, Button, Menu, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconLogout } from '@tabler/icons-react';
import { useState } from 'react';
import { useAuth } from '../auth/context';

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : (parts[0] ?? '?').slice(0, 2)).toUpperCase();
}

/** Header menu of the logged-in user (server edition): name, e-mail, Log out. Nothing in the local edition. */
export function UserMenu() {
  const { mode, user, logout } = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);
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
        <Menu.Item leftSection={<IconLogout size={16} />} onClick={onLogout} disabled={loggingOut}>
          {loggingOut ? 'Logging out…' : 'Log out'}
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
