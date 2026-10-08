import { Button, Group, Text } from '@mantine/core';
import { IconLinkPlus, IconX } from '@tabler/icons-react';
import { useAppActions } from '../actions/context';

/**
 * The bar shown while a two-step link is pending ("Start link"): which
 * requirement links from, how to finish (right-click a target → "Make link")
 * and how to cancel. Shown in main and detached windows alike, above the page
 * area (it takes its own row, never covering the tab bar or side panels).
 */
export function PendingLinkBar() {
  const { state, run, isAvailable } = useAppActions();
  const source = state.pendingLink;
  if (!source) return null;

  // Another project (or module) than the open one: say where the source is.
  const where =
    source.projectId !== state.projectId
      ? [source.projectName ?? source.projectId, source.moduleName ?? source.moduleId].join(' · ')
      : source.moduleId !== state.moduleId
        ? (source.moduleName ?? source.moduleId)
        : null;

  return (
    <Group className="reqman-pending-link-bar" role="status" aria-live="polite" gap="xs" wrap="nowrap">
      <IconLinkPlus size={16} aria-hidden style={{ flex: 'none' }} />
      <Text size="sm" truncate="end" style={{ flex: 1, minWidth: 0 }}>
        Linking from <b>{source.requirementId}</b>
        {where ? ` (${where})` : ''} — pick a target requirement: right-click it → Make link (Esc to cancel)
      </Text>
      <Button
        size="compact-xs"
        variant="default"
        leftSection={<IconX size={12} />}
        disabled={!isAvailable('edit.cancelLink')}
        onClick={() => run('edit.cancelLink')}
      >
        Cancel link
      </Button>
    </Group>
  );
}
