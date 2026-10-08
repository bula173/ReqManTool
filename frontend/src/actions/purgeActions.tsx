import { Text } from '@mantine/core';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import { IconTrash, IconTrashX } from '@tabler/icons-react';
import { ApiError } from '../api/client';
import type { Action } from './types';

/**
 * Purge: hard-delete every requirement of the open module or of the whole
 * open project (no trash, no undo; see the backlog's purge semantics). Both
 * ask for confirmation first and capture the ids when the dialog opens, so
 * confirming purges what the dialog names even if the route changed since.
 * Application-wide (`app`) actions: the menu bar uses them today, and a
 * module/project context menu can list the same ids later.
 */

function notifyPurged(purged: number) {
  notifications.show({ message: `Purged ${purged} requirement(s)`, color: 'green' });
}

function notifyError(err: unknown, fallback: string) {
  notifications.show({ message: err instanceof ApiError ? err.message : fallback, color: 'red' });
}

export const purgeActions: Action<'app'>[] = [
  {
    id: 'module.purge',
    element: 'app',
    edits: true,
    editsModule: true,
    label: 'Purge module',
    icon: IconTrash,
    color: 'red',
    opensDialog: true,
    isAvailable: (ctx) => ctx.projectId !== null && ctx.module !== null,
    run: (ctx) => {
      const { projectId, module, commands } = ctx;
      if (!projectId || !module) return;
      modals.openConfirmModal({
        title: 'Purge module',
        children: (
          <Text size="sm">
            Are you sure you want to delete ALL requirements in "{module.name}"? Their links are deleted too,
            including links to them from other projects that reference this one. This cannot be undone.
          </Text>
        ),
        labels: { confirm: 'Purge', cancel: 'Cancel' },
        confirmProps: { color: 'red' },
        onConfirm: () => {
          commands.purgeModule(projectId, module.id).then(
            (result) => notifyPurged(result.purged),
            (err) => notifyError(err, 'Failed to purge module'),
          );
        },
      });
    },
  },
  {
    id: 'project.purge',
    requires: ['projectAdmin'],
    element: 'app',
    edits: true,
    label: 'Purge project',
    icon: IconTrashX,
    color: 'red',
    opensDialog: true,
    isAvailable: (ctx) => ctx.project !== null,
    run: (ctx) => {
      const { project, commands } = ctx;
      if (!project) return;
      modals.openConfirmModal({
        title: 'Purge project',
        children: (
          <Text size="sm">
            Are you sure you want to delete ALL requirements in every module of "{project.name}"? Their links
            are deleted too, including links to them from other projects that reference this one. This cannot
            be undone.
          </Text>
        ),
        labels: { confirm: 'Purge', cancel: 'Cancel' },
        confirmProps: { color: 'red' },
        onConfirm: () => {
          commands.purgeProject(project.id).then(
            (result) => notifyPurged(result.purged),
            (err) => notifyError(err, 'Failed to purge project'),
          );
        },
      });
    },
  },
];
