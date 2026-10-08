import { ActionIcon, AppShell, Group, Title } from '@mantine/core';
import { IconMoon, IconSun } from '@tabler/icons-react';
import { Outlet } from 'react-router-dom';
import { AppActionsProvider } from '../actions/AppActionsProvider';
import { useAppActions } from '../actions/context';
import { useAuth } from '../auth/context';
import { DataDirSettings } from '../components/DataDirSettings';
import { UserMenu } from '../components/UserMenu';
import { VersionFooter } from '../components/VersionFooter';
import { useLiveSync } from '../hooks/useLiveSync';
import { MenuBar } from './MenuBar';
import { ModuleTabBar } from './ModuleTabBar';
import { PendingLinkBar } from './PendingLinkBar';
import { LeftSidePanel } from './SidePanelLayout';
import { SIDE_PANEL_RAIL_WIDTH, usePanelWidth } from './sidePanels';

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

/**
 * The fixed app shell. The navbar holds the collapsible, resizable left panel
 * (its rail when collapsed or on a narrow screen, where the open panel is an
 * overlay instead); the main area takes the rest of the width. The narrow
 * layout is handled by the side panels themselves, not by AppShell.
 */
function AppFrame() {
  const { state } = useAppActions();
  const { canManageServer } = useAuth();
  const leftWidth = usePanelWidth('left');
  const navbarWidth = state.leftPanelOpen && !state.narrowLayout ? leftWidth.width : SIDE_PANEL_RAIL_WIDTH;

  return (
    <AppShell
      header={{ height: 56 }}
      // Breakpoint 0: Mantine's own mobile navbar (full width) never applies.
      navbar={{ width: navbarWidth, breakpoint: 0 }}
      // No animation while the border is dragged, so the panel follows the pointer.
      transitionDuration={leftWidth.resizing ? 0 : 150}
      footer={{ height: 28 }}
      // The page area (.reqman-page) carries the padding, so its scrollbar sits on the edge.
      padding={0}
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="md" wrap="nowrap">
            <Title order={3}>ReqManTool</Title>
            <MenuBar />
          </Group>
          <Group gap="xs" wrap="nowrap">
            {/* Server edition: switching the server's data folder is for server admins only. */}
            {canManageServer && <DataDirSettings />}
            <ColorSchemeToggle />
            <UserMenu />
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar>
        <LeftSidePanel width={leftWidth} />
      </AppShell.Navbar>
      {/* Fixed shell: only the page area (and the panels inside it) scrolls, never the document. */}
      <AppShell.Main className="reqman-main">
        <ModuleTabBar />
        <PendingLinkBar />
        <div className="reqman-page">
          <Outlet />
        </div>
      </AppShell.Main>
      <AppShell.Footer>
        <VersionFooter />
      </AppShell.Footer>
    </AppShell>
  );
}

export function AppLayout() {
  useLiveSync();

  return (
    <AppActionsProvider>
      <AppFrame />
    </AppActionsProvider>
  );
}
