import { Anchor, Badge, Button, Group, Menu, Text, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconAppWindow, IconNetwork, IconServer } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { getDesktopState, isDesktop, requestModeSwitch, type DesktopState } from '../api/desktop';
import { useSourceCode } from '../hooks/useSourceCode';
import { useVersionStatus } from '../hooks/useVersionStatus';

function hostOf(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).host;
  } catch {
    return null;
  }
}

/**
 * Desktop-only: where this window gets its requirements from — the same choice
 * the start screen offers, reachable without leaving the app. Hidden outside
 * the desktop app, where there is no local backend to switch to.
 */
function DesktopModeControl() {
  const [state, setState] = useState<DesktopState | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getDesktopState()
      .then((next) => {
        if (!cancelled) setState(next);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const switchTo = async (target: 'local' | 'ask') => {
    setBusy(true);
    try {
      await requestModeSwitch(target);
    } catch (error) {
      console.warn('[reqman] switching the startup mode failed', error);
    } finally {
      setBusy(false);
    }
  };

  const label =
    state?.mode === 'local'
      ? 'Local instance'
      : state?.mode === 'server'
        ? hostOf(state.serverUrl) ?? 'Server'
        : 'Mode…';

  return (
    <Menu shadow="md" width={220}>
      <Menu.Target>
        <Button
          type="button"
          variant="subtle"
          size="compact-xs"
          leftSection={<IconServer size={13} />}
          loading={busy}
        >
          {label}
        </Button>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>Requirements come from</Menu.Label>
        <Menu.Item
          leftSection={<IconAppWindow size={14} />}
          disabled={busy || state?.mode === 'local'}
          onClick={() => void switchTo('local')}
        >
          Local instance
        </Menu.Item>
        <Menu.Item
          leftSection={<IconNetwork size={14} />}
          disabled={busy || state?.mode === 'server'}
          onClick={() => void switchTo('ask')}
        >
          Server address…
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

/**
 * App-shell footer: this frontend's version/build, plus a warning when the
 * backend reports a different tool version, and the "Source code" link of
 * the running build (AGPL-3.0 §13). Never throws: while the backend
 * version loads, or if /version fails, only the frontend part is shown.
 */
export function VersionFooter() {
  const { frontend, backend, mismatch, backendLabel } = useVersionStatus();
  const source = useSourceCode();
  const desktop = isDesktop();

  return (
    <Group h="100%" px="md" gap="xs" justify="flex-end" wrap="nowrap">
      {desktop && <DesktopModeControl />}
      <Group gap="xs" justify="flex-end" wrap="nowrap" ml="auto">
        <Tooltip label={backendLabel}>
          <Text size="xs" c="dimmed" data-testid="version-footer">
            ReqManTool {frontend.version} · build {frontend.build}
          </Text>
        </Tooltip>
        <Tooltip label={source.tooltip}>
          <Anchor
            size="xs"
            href={source.link}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="footer-source-link"
          >
            Source code
          </Anchor>
        </Tooltip>
        {backend.isError && !backend.data && (
          <Text size="xs" c="dimmed" fs="italic">
            (backend version unavailable)
          </Text>
        )}
        {mismatch && backend.data && (
          <Tooltip
            multiline
            w={320}
            label={`This page was built for ${frontend.version}, but the backend runs ${backend.data.version} (build ${backend.data.build}). Reload the page; if the warning persists, rebuild the frontend.`}
          >
            <Badge color="orange" variant="light" size="sm" leftSection={<IconAlertTriangle size={12} />}>
              Version mismatch: backend {backend.data.version}
            </Badge>
          </Tooltip>
        )}
      </Group>
    </Group>
  );
}
