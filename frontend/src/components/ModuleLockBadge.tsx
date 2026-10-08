import { Badge, Tooltip } from '@mantine/core';
import { IconLock } from '@tabler/icons-react';
import { describeModuleLock } from '../api/moduleLocks';
import type { ModuleLock } from '../api/types';

/**
 * Server edition: the edit lock badge of a checked-out module (nothing when
 * free). "Checked out" when it is the user's own lock, "Locked by X"
 * otherwise; the tooltip says who, since when and why. `compact`: an icon
 * only (sidebar tree, tab bar).
 */
export function ModuleLockBadge({
  lock,
  userId,
  compact = false,
}: {
  lock: ModuleLock | null | undefined;
  userId: string | null;
  compact?: boolean;
}) {
  if (!lock) return null;
  const mine = lock.user_id === userId;
  const description = mine ? `You have this module checked out. ${describeModuleLock(lock)}` : describeModuleLock(lock);
  return (
    <Tooltip label={description} multiline w={320} withArrow>
      <Badge
        component="span"
        color={mine ? 'teal' : 'orange'}
        variant="light"
        size={compact ? 'xs' : 'md'}
        leftSection={<IconLock size={compact ? 10 : 12} />}
        aria-label={description}
        style={{ flexShrink: 0 }}
      >
        {compact ? '' : mine ? 'Checked out by you' : `Locked by ${lock.user_name}`}
      </Badge>
    </Tooltip>
  );
}
