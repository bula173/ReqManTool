import { ActionIcon, Button, Group, Text, Tooltip } from '@mantine/core';
import { IconLayoutNavbarExpand, IconMoon, IconSun } from '@tabler/icons-react';
import { useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import { AppActionsProvider } from '../actions/AppActionsProvider';
import { useAppActions } from '../actions/context';
import { useLiveSync } from '../hooks/useLiveSync';
import { PendingLinkBar } from './PendingLinkBar';

/** The slim header of a detached window: what is shown, Re-attach, colour scheme. */
function DetachedHeader() {
  const { state, run, isAvailable } = useAppActions();
  const projectName = state.project?.name ?? state.projectId ?? '';
  const moduleName = state.module?.name ?? state.moduleId ?? '';
  const title = projectName && moduleName ? `${projectName} · ${moduleName}` : 'ReqManTool';

  useEffect(() => {
    const previous = document.title;
    document.title = `${title} — ReqManTool`;
    return () => {
      document.title = previous;
    };
  }, [title]);

  return (
    <Group className="reqman-detached-header" justify="space-between" wrap="nowrap">
      <Text fw={600} truncate="end">
        {title}
      </Text>
      <Group gap="xs" wrap="nowrap">
        <Tooltip label="Show this module as a tab of the main window and close this window">
          <Button
            size="xs"
            variant="default"
            leftSection={<IconLayoutNavbarExpand size={14} />}
            disabled={!isAvailable('window.reattach')}
            onClick={() => run('window.reattach')}
          >
            Re-attach
          </Button>
        </Tooltip>
        <ActionIcon
          variant="default"
          aria-label="Toggle color scheme"
          onClick={() => run('view.toggleDarkMode')}
        >
          {state.colorScheme === 'dark' ? <IconSun size={16} /> : <IconMoon size={16} />}
        </ActionIcon>
      </Group>
    </Group>
  );
}

/**
 * A detached module window (`/detached/projects/:projectId/modules/:moduleId`):
 * the module page with minimal chrome — no menu bar, navigation panel or tab
 * bar. Its own live-sync connection keeps it current; the keyboard shortcuts,
 * context menus and dialogs work as in the main window. Re-attach moves the
 * module back into the main window's tab bar.
 */
export function DetachedLayout() {
  useLiveSync();

  return (
    <AppActionsProvider>
      <div className="reqman-detached">
        <DetachedHeader />
        <PendingLinkBar />
        <div className="reqman-page">
          <Outlet />
        </div>
      </div>
    </AppActionsProvider>
  );
}
