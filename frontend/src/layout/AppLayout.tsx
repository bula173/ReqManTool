import { ActionIcon, AppShell, Group, Title } from '@mantine/core';
import { IconMoon, IconSun } from '@tabler/icons-react';
import { Outlet } from 'react-router-dom';
import { AppActionsProvider } from '../actions/AppActionsProvider';
import { useAppActions } from '../actions/context';
import { DataDirSettings } from '../components/DataDirSettings';
import { Sidebar } from '../components/Sidebar';
import { VersionFooter } from '../components/VersionFooter';
import { useLiveSync } from '../hooks/useLiveSync';
import { MenuBar } from './MenuBar';

/** Header button for View ▸ Dark mode (same registry action). */
function ColorSchemeToggle() {
  const { state, run } = useAppActions();
  const dark = state.colorScheme === 'dark';

  return (
    <ActionIcon variant="default" size="lg" aria-label="Toggle color scheme" onClick={() => run('view.toggleDarkMode')}>
      {dark ? <IconSun size={18} /> : <IconMoon size={18} />}
    </ActionIcon>
  );
}

export function AppLayout() {
  useLiveSync();

  return (
    <AppActionsProvider>
      <AppShell
        header={{ height: 56 }}
        navbar={{ width: 280, breakpoint: 'sm' }}
        footer={{ height: 28 }}
        padding="md"
      >
        <AppShell.Header>
          <Group h="100%" px="md" justify="space-between" wrap="nowrap">
            <Group gap="md" wrap="nowrap">
              <Title order={3}>ReqManTool</Title>
              <MenuBar />
            </Group>
            <Group gap="xs" wrap="nowrap">
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
    </AppActionsProvider>
  );
}
