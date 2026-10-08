import { Text } from '@mantine/core';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import {
  IconArrowDown,
  IconArrowRight,
  IconArrowUp,
  IconArrowsMove,
  IconClipboard,
  IconClipboardPlus,
  IconCopy,
  IconCut,
  IconLayoutSidebarRight,
  IconLink,
  IconListTree,
  IconPlus,
  IconRowInsertBottom,
  IconSubtask,
  IconTrash,
} from '@tabler/icons-react';
import { ApiError } from '../api/client';
import type { Requirement } from '../api/types';
import { linksOf } from './links';
import type { Action, ActionContext } from './types';

/**
 * Actions on one requirement of the open module. The target is the clicked
 * row (context menus, row buttons) or the selected requirement (menu bar,
 * shortcuts). The server owns every rule (ids, cycles, ordering); these
 * actions only decide what is offered and report the outcome.
 *
 * Two menus use them (`registry.ts`): the right-click editing menu (cut/copy/
 * paste, add, move, link, delete) and the left-click navigation menu (show
 * in tree, details, go to linked requirement).
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

/** Sibling order as the server sorts it: by `order`, ties by id (code point order). */
function bySiblingOrder(a: Requirement, b: Requirement): number {
  if (a.order !== b.order) return a.order - b.order;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

function siblingsOf(ctx: RequirementContext, requirement: Requirement): Requirement[] {
  return ctx.requirements.filter((r) => r.parent_id === requirement.parent_id).sort(bySiblingOrder);
}

/**
 * The 0-based sibling position right after `requirement`, for the server's
 * `move` with an index (positions count the new parent's children without
 * the moved requirement, hence `movingId`).
 */
function indexAfter(ctx: RequirementContext, requirement: Requirement, movingId?: string): number {
  const siblings = siblingsOf(ctx, requirement).filter((r) => r.id !== movingId);
  return siblings.findIndex((r) => r.id === requirement.id) + 1;
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

/**
 * Pastes the clipboard as a sibling right after `anchor`, through the
 * existing server operations: a copy (appended under the anchor's parent) is
 * then moved to the position after the anchor; a cut is moved there directly.
 */
function pasteAfter(ctx: RequirementContext, anchor: Requirement) {
  const clipboard = pasteableClipboard(ctx);
  if (!clipboard) return;
  const { mode, requirement } = clipboard;
  const { commands } = ctx;
  if (mode === 'copy') {
    const index = indexAfter(ctx, anchor);
    commands.requirements.copy(requirement.id, anchor.parent_id).then(
      (created) =>
        commands.requirements.move(created.id, anchor.parent_id, index).then(
          () => {
            notifications.show({ message: `Pasted a copy of ${requirement.id} after ${anchor.id}`, color: 'green' });
            commands.selectRequirement(created.id);
          },
          (err) =>
            notifyError(err, `Pasted a copy of ${requirement.id} as the last sibling (it could not be placed after ${anchor.id})`),
        ),
      (err) => notifyError(err, 'Failed to paste requirement'),
    );
  } else {
    commands.requirements.move(requirement.id, anchor.parent_id, indexAfter(ctx, anchor, requirement.id)).then(
      () => {
        notifications.show({ message: `Moved ${requirement.id} after ${anchor.id}`, color: 'green' });
        commands.setClipboard(null);
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

function goTo(ctx: RequirementContext, requirementId: string) {
  ctx.commands.goToRequirement(requirementId).catch((err) => notifyError(err, 'Failed to locate linked requirement'));
}

function targetLinks(ctx: RequirementContext) {
  const requirement = targetOf(ctx);
  return requirement && ctx.project ? linksOf(requirement, ctx.project.attributes) : [];
}

export const requirementActions: Action<'requirement'>[] = [
  // --- editing (right-click menu, Edit menu) ---------------------------------
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
    // Not onto the clipboard requirement itself (Ctrl/Cmd+V then pastes it as
    // a sibling, see below); every other rule (e.g. not into its own subtree)
    // is the server's.
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
    // Registered after "Paste as child": Ctrl/Cmd+V reaches it only when that
    // one is unavailable, i.e. with the copied requirement itself selected.
    id: 'requirement.pasteAsSibling',
    element: 'requirement',
    label: 'Paste as sibling',
    icon: IconClipboardPlus,
    shortcuts: [{ key: 'v', mod: true }],
    // A cut requirement cannot be pasted right after itself (it is already there).
    isAvailable: (ctx) => {
      const requirement = targetOf(ctx);
      const clipboard = pasteableClipboard(ctx);
      return (
        requirement !== null &&
        clipboard !== null &&
        !(clipboard.mode === 'cut' && clipboard.requirement.id === requirement.id)
      );
    },
    run: (ctx) => {
      const requirement = targetOf(ctx);
      if (requirement) pasteAfter(ctx, requirement);
    },
  },
  {
    id: 'requirement.pasteAtRoot',
    element: 'requirement',
    label: 'Paste at root',
    icon: IconClipboard,
    // Ctrl/Cmd+V falls back to this only when no requirement is selected;
    // with a selection it is "Paste as child"/"as sibling" (`whenNoTarget`).
    shortcuts: [
      { key: 'v', mod: true, shift: true },
      { key: 'v', mod: true, whenNoTarget: true },
    ],
    isAvailable: (ctx) => pasteableClipboard(ctx) !== null,
    run: (ctx) => paste(ctx, null),
  },
  {
    // The module page's "Add Requirement" button: a new top-level requirement.
    id: 'requirement.addTopLevel',
    element: 'requirement',
    label: 'New requirement',
    icon: IconPlus,
    opensDialog: true,
    isAvailable: (ctx) => ctx.projectId !== null && ctx.module !== null,
    run: (ctx) => {
      if (!ctx.projectId || !ctx.module) return;
      ctx.commands.openDialog({ kind: 'addRequirement', projectId: ctx.projectId, moduleId: ctx.module.id, parentId: null });
    },
  },
  {
    id: 'requirement.addChild',
    element: 'requirement',
    label: 'Add child',
    icon: IconSubtask,
    opensDialog: true,
    isAvailable: (ctx) => targetOf(ctx) !== null,
    run: (ctx) => {
      const requirement = targetOf(ctx);
      if (!requirement || !ctx.projectId || !ctx.module) return;
      ctx.commands.openDialog({
        kind: 'addRequirement',
        projectId: ctx.projectId,
        moduleId: ctx.module.id,
        parentId: requirement.id,
      });
    },
  },
  {
    // A new requirement under the same parent, placed right after the target.
    id: 'requirement.addSibling',
    element: 'requirement',
    label: 'Add sibling',
    icon: IconRowInsertBottom,
    opensDialog: true,
    isAvailable: (ctx) => targetOf(ctx) !== null,
    run: (ctx) => {
      const requirement = targetOf(ctx);
      if (!requirement || !ctx.projectId || !ctx.module) return;
      ctx.commands.openDialog({
        kind: 'addRequirement',
        projectId: ctx.projectId,
        moduleId: ctx.module.id,
        parentId: requirement.parent_id,
        insertIndex: indexAfter(ctx, requirement),
      });
    },
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
    id: 'requirement.moveTo',
    element: 'requirement',
    label: 'Move to parent',
    icon: IconArrowsMove,
    opensDialog: true,
    isAvailable: (ctx) => targetOf(ctx) !== null,
    run: (ctx) => {
      const requirement = targetOf(ctx);
      if (!requirement || !ctx.projectId || !ctx.module) return;
      ctx.commands.openDialog({
        kind: 'moveRequirement',
        projectId: ctx.projectId,
        moduleId: ctx.module.id,
        source: requirement,
      });
    },
  },
  {
    id: 'requirement.link',
    element: 'requirement',
    label: 'Link to',
    icon: IconLink,
    opensDialog: true,
    isAvailable: (ctx) => targetOf(ctx) !== null,
    run: (ctx) => {
      const requirement = targetOf(ctx);
      if (!requirement || !ctx.projectId || !ctx.module) return;
      ctx.commands.openDialog({
        kind: 'linkRequirement',
        projectId: ctx.projectId,
        moduleId: ctx.module.id,
        source: requirement,
      });
    },
  },
  {
    id: 'requirement.delete',
    element: 'requirement',
    label: 'Delete',
    icon: IconTrash,
    color: 'red',
    opensDialog: true,
    // Delete; ⌘⌫ too, since Mac keyboards often lack a forward-delete key.
    shortcuts: [{ key: 'Delete' }, { key: 'Backspace', mod: true }],
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

  // --- navigation (left-click menu) ------------------------------------------
  {
    id: 'requirement.showInTree',
    element: 'requirement',
    label: 'Show in tree',
    icon: IconListTree,
    isAvailable: (ctx) => targetOf(ctx) !== null,
    run: (ctx) => {
      const requirement = targetOf(ctx);
      if (requirement) ctx.commands.showInTree(requirement.id);
    },
  },
  {
    id: 'requirement.showDetails',
    element: 'requirement',
    label: 'Show details',
    icon: IconLayoutSidebarRight,
    isAvailable: (ctx) => targetOf(ctx) !== null,
    run: (ctx) => {
      const requirement = targetOf(ctx);
      if (!requirement) return;
      ctx.commands.selectRequirement(requirement.id);
      ctx.commands.setDetailPanelOpen(true);
    },
  },
  {
    // "Show links": one entry per `>`/`<` link of the requirement.
    id: 'requirement.goToLinked',
    element: 'requirement',
    label: 'Go to linked requirement',
    icon: IconArrowRight,
    isAvailable: (ctx) => targetLinks(ctx).length > 0,
    run: (ctx) => {
      const first = targetLinks(ctx)[0];
      if (first) goTo(ctx, first.otherId);
    },
    items: (ctx) =>
      targetLinks(ctx).map((link, index) => ({
        key: `${link.configId}:${link.direction}:${link.otherId}:${index}`,
        label: `${link.direction === 'out' ? '>' : '<'} ${link.otherId} (${link.configName})`,
        run: () => goTo(ctx, link.otherId),
      })),
  },
];
