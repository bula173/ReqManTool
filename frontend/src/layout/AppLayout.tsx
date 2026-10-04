import { ActionIcon, AppShell, Group, Title, useMantineColorScheme, useComputedColorScheme } from '@mantine/core';
import { IconMoon, IconSun } from '@tabler/icons-react';
import { Outlet } from 'react-router-dom';
import { DataDirSettings } from '../components/DataDirSettings';
import { Sidebar } from '../components/Sidebar';
import { VersionFooter } from '../components/VersionFooter';
import { useLiveSync } from '../hooks/useLiveSync';

function ColorSchemeToggle() {
  const { setColorScheme } = useMantineColorScheme();
  const computedColorScheme = useComputedColorScheme('light');

  return (
    <ActionIcon
      variant="default"
      size="lg"
      aria-label="Toggle color scheme"
      onClick={() => setColorScheme(computedColorScheme === 'dark' ? 'light' : 'dark')}
    >
      {computedColorScheme === 'dark' ? <IconSun size={18} /> : <IconMoon size={18} />}
    </ActionIcon>
  );
}

export function AppLayout() {
  useLiveSync();

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 280, breakpoint: 'sm' }}
      footer={{ height: 28 }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Title order={3}>ReqManTool</Title>
          <Group gap="xs">
            <DataDirSettings />
            <ColorSchemeToggle />
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar>
        <Sidebar />
      </AppShell.Navbar>
      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
      <AppShell.Footer>
        <VersionFooter />
      </AppShell.Footer>
    </AppShell>
  );
}
