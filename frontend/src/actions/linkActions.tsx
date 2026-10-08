import { Text } from '@mantine/core';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import { IconArrowRight, IconLinkOff } from '@tabler/icons-react';
import { ApiError } from '../api/client';
import type { Action, ActionContext, LinkTarget } from './types';

/**
 * Actions on one `>`/`<` link badge of a requirement (left click: go to;
 * right click: go to / remove link). Links have no default target: they are
 * only reachable from a badge's menus.
 */

type LinkContext = ActionContext<'link'>;

function notifyError(err: unknown, fallback: string) {
  notifications.show({ message: err instanceof ApiError ? err.message : fallback, color: 'red' });
}

/** The link, if its requirement belongs to the open module and its attribute is a link attribute. */
function linkOf(ctx: LinkContext): LinkTarget | null {
  const link = ctx.target;
  if (!link || !ctx.project) return null;
  return ctx.project.attributes.some((a) => a.system && a.id === link.configId) ? link : null;
}

export const linkActions: Action<'link'>[] = [
  {
    id: 'link.goTo',
    element: 'link',
    label: 'Go to linked requirement',
    icon: IconArrowRight,
    isAvailable: (ctx) => linkOf(ctx) !== null,
    run: (ctx) => {
      const link = linkOf(ctx);
      if (!link) return;
      ctx.commands
        .goToRequirement(link.otherId)
        .catch((err) => notifyError(err, 'Failed to locate linked requirement'));
    },
  },
  {
    id: 'link.remove',
    element: 'link',
    label: 'Remove link',
    icon: IconLinkOff,
    color: 'red',
    opensDialog: true,
    isAvailable: (ctx) => linkOf(ctx) !== null,
    run: (ctx) => {
      const link = linkOf(ctx);
      if (!link) return;
      const sourceId = link.direction === 'out' ? link.requirement.id : link.otherId;
      const targetId = link.direction === 'out' ? link.otherId : link.requirement.id;
      const { commands } = ctx;
      modals.openConfirmModal({
        title: 'Remove link',
        children: (
          <Text size="sm">
            Remove the link from "{sourceId}" to "{targetId}"? Both requirements' link attributes are updated.
          </Text>
        ),
        labels: { confirm: 'Remove', cancel: 'Cancel' },
        confirmProps: { color: 'red' },
        onConfirm: () => {
          commands.unlink(link.configId, sourceId, targetId).then(
            () => notifications.show({ message: `Removed the link ${sourceId} -> ${targetId}`, color: 'green' }),
            (err) => notifyError(err, 'Failed to remove link'),
          );
        },
      });
    },
  },
];
