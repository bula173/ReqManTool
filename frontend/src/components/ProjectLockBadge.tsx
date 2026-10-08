import { Badge, Tooltip } from '@mantine/core';
import { IconLock } from '@tabler/icons-react';
import type { ProjectLock } from '../api/types';
import { describeLock } from './projectLock';

/** The lock badge of a locked project (nothing when unlocked); the tooltip says who, when and why. */
export function ProjectLockBadge({ lock, compact = false }: { lock: ProjectLock | null | undefined; compact?: boolean }) {
  if (!lock) return null;
  return (
    <Tooltip label={describeLock(lock)} multiline w={320} withArrow>
      <Badge
        component="span"
        color="orange"
        variant="light"
        size={compact ? 'xs' : 'md'}
        leftSection={<IconLock size={compact ? 10 : 12} />}
        aria-label={describeLock(lock)}
        style={{ flexShrink: 0 }}
      >
        {compact ? '' : 'Locked'}
      </Badge>
    </Tooltip>
  );
}
