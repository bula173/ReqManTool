import { Text } from '@mantine/core';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import { IconArrowDown, IconArrowUp, IconClipboard, IconCopy, IconCut, IconTrash } from '@tabler/icons-react';
import { ApiError } from '../api/client';
import type { Requirement } from '../api/types';
import type { Action, ActionContext } from './types';

/**
 * Actions on one requirement of the open module. The target is the
 * right-clicked row (context menu) or the selected requirement (menu bar,
 * shortcuts). The server owns every rule (ids, cycles, ordering); these
 * actions only decide what is offered and report the outcome.
 */

type RequirementContext = ActionContext<'requirement'>;

function notifyError(err: unknown, fallback: string) {
  notifications.show({ message: err instanceof ApiError ? err.message : fallback, color: 'red' });
}

/** The target requirement, if it belongs to the open module. */
function targetOf(ctx: RequirementContext): Requirement | null {
  if (!ctx.module || !ctx.target) return null;
  return ctx.requirements.some((r) => r.id === ctx.target!.id) ? ctx.target : null;
}

/** The clipboard, if it can be pasted into the open module (copy/move are module-scoped). */
function pasteableClipboard(ctx: RequirementContext) {
  const { clipboard, module, projectId } = ctx;
  if (!clipboard || !module || clipboard.projectId !== projectId || clipboard.moduleId !== module.id) return null;
  return clipboard;
}

function siblingsOf(ctx: RequirementContext, requirement: Requirement): Requirement[] {
  return ctx.requirements
    .filter((r) => r.parent_id === requirement.parent_id)
    .sort((a, b) => a.order - b.order);
}

function paste(ctx: RequirementContext, targetParentId: string | null) {
  const clipboard = pasteableClipboard(ctx);
  if (!clipboard) return;
  const { mode, requirement } = clipboard;
  if (mode === 'copy') {
    ctx.commands.requirements.copy(requirement.id, targetParentId).then(
      () => notifications.show({ message: `Pasted a copy of ${requirement.id}`, color: 'green' }),
      (err) => notifyError(err, 'Failed to paste requirement'),
    );
  } else {
    ctx.commands.requirements.move(requirement.id, targetParentId).then(
      () => {
        notifications.show({ message: `Moved ${requirement.id}`, color: 'green' });
        ctx.commands.setClipboard(null);
      },
      (err) => notifyError(err, 'Failed to move requirement'),
    );
  }
}

function reorder(ctx: RequirementContext, direction: 'up' | 'down') {
  const requirement = targetOf(ctx);
  if (!requirement) return;
  ctx.commands.requirements.reorder(requirement.id, direction).catch((err) =>
    notifyError(err, 'Failed to reorder requirement'),
  );
}

export const requirementActions: Action<'requirement'>[] = [
  {
    id: 'requirement.cut',
    element: 'requirement',
    label: 'Cut',
    icon: IconCut,
    shortcuts: [{ key: 'x', mod: true }],
    isAvailable: (ctx) => targetOf(ctx) !== null,
    run: (ctx) => {
      const requirement = targetOf(ctx);
      if (!requirement || !ctx.projectId || !ctx.module) return;
      ctx.commands.setClipboard({ mode: 'cut', projectId: ctx.projectId, moduleId: ctx.module.id, requirement });
    },
  },
  {
    id: 'requirement.copy',
    element: 'requirement',
    label: 'Copy',
    icon: IconCopy,
    shortcuts: [{ key: 'c', mod: true }],
    isAvailable: (ctx) => targetOf(ctx) !== null,
    run: (ctx) => {
      const requirement = targetOf(ctx);
      if (!requirement || !ctx.projectId || !ctx.module) return;
      ctx.commands.setClipboard({ mode: 'copy', projectId: ctx.projectId, moduleId: ctx.module.id, requirement });
    },
  },
  {
    id: 'requirement.pasteAsChild',
    element: 'requirement',
    label: 'Paste as child',
    icon: IconClipboard,
    shortcuts: [{ key: 'v', mod: true }],
    // Not onto the clipboard requirement itself; every other rule (e.g. not
    // into its own subtree) is the server's.
    isAvailable: (ctx) => {
      const requirement = targetOf(ctx);
      const clipboard = pasteableClipboard(ctx);
      return requirement !== null && clipboard !== null && clipboard.requirement.id !== requirement.id;
    },
    run: (ctx) => {
      const requirement = targetOf(ctx);
      const clipboard = pasteableClipboard(ctx);
      if (requirement && clipboard && clipboard.requirement.id !== requirement.id) paste(ctx, requirement.id);
    },
  },
  {
    id: 'requirement.pasteAtRoot',
    element: 'requirement',
    label: 'Paste at root',
    icon: IconClipboard,
    // Ctrl/Cmd+V falls back to this only when no requirement is selected;
    // with a selection it is "Paste as child" or nothing (`whenNoTarget`).
    shortcuts: [
      { key: 'v', mod: true, shift: true },
      { key: 'v', mod: true, whenNoTarget: true },
    ],
    isAvailable: (ctx) => pasteableClipboard(ctx) !== null,
    run: (ctx) => paste(ctx, null),
  },
  {
    id: 'requirement.moveUp',
    element: 'requirement',
    label: 'Move Up',
    icon: IconArrowUp,
    shortcuts: [{ key: 'ArrowUp', alt: true }],
    isAvailable: (ctx) => {
      const requirement = targetOf(ctx);
      return requirement !== null && siblingsOf(ctx, requirement)[0]?.id !== requirement.id;
    },
    run: (ctx) => reorder(ctx, 'up'),
  },
  {
    id: 'requirement.moveDown',
    element: 'requirement',
    label: 'Move Down',
    icon: IconArrowDown,
    shortcuts: [{ key: 'ArrowDown', alt: true }],
    isAvailable: (ctx) => {
      const requirement = targetOf(ctx);
      return requirement !== null && siblingsOf(ctx, requirement).at(-1)?.id !== requirement.id;
    },
    run: (ctx) => reorder(ctx, 'down'),
  },
  {
    id: 'requirement.delete',
    element: 'requirement',
    label: 'Delete',
    icon: IconTrash,
    color: 'red',
    opensDialog: true,
    isAvailable: (ctx) => targetOf(ctx) !== null,
    run: (ctx) => {
      const requirement = targetOf(ctx);
      if (!requirement) return;
      const { commands } = ctx;
      modals.openConfirmModal({
        title: 'Remove requirement',
        children: (
          <Text size="sm">
            Are you sure you want to remove requirement "{requirement.id}"? Its children move up one level. This
            cannot be undone.
          </Text>
        ),
        labels: { confirm: 'Remove', cancel: 'Cancel' },
        confirmProps: { color: 'red' },
        onConfirm: () => {
          commands.requirements.remove(requirement.id).then(
            () => notifications.show({ message: `Removed ${requirement.id}`, color: 'green' }),
            (err) => notifyError(err, 'Failed to remove requirement'),
          );
        },
      });
    },
  },
];
