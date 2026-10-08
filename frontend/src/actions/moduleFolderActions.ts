import { notifications } from '@mantine/notifications';
import {
  IconArrowDown,
  IconArrowUp,
  IconFolderPlus,
  IconFolderSymlink,
  IconFolderX,
  IconPencil,
  IconPlus,
  IconTemplate,
} from '@tabler/icons-react';
import { ApiError } from '../api/client';
import { isProjectStub } from '../api/types';
import type { Action, ActionContext } from './types';

/**
 * Module folder actions: "New folder" on a project node, "Move to folder" on
 * a module node, and the folder node's own menu. Folders are purely
 * organisational; the server owns every rule (ids, names, nesting, empty-only
 * delete) and these actions only open dialogs or call it.
 */

type FolderContext = ActionContext<'folder'>;

function notifyFailure(fallback: string) {
  return (err: unknown) =>
    notifications.show({ message: err instanceof ApiError ? err.message : fallback, color: 'red' });
}

function reorderFolder(ctx: FolderContext, direction: 'up' | 'down') {
  const target = ctx.target;
  if (!target) return;
  ctx.commands.moduleFolders
    .reorder(target.projectId, target.folder.folder, direction)
    .catch(notifyFailure('Failed to move folder'));
}

export const projectFolderActions: Action<'project'>[] = [
  {
    id: 'project.newFolder',
    element: 'project',
    label: 'New folder',
    icon: IconFolderPlus,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null && !isProjectStub(ctx.target),
    run: (ctx) => {
      const project = ctx.target;
      if (project && !isProjectStub(project)) {
        ctx.commands.openDialog({ kind: 'newModuleFolder', projectId: project.id, parentId: null });
      }
    },
  },
];

export const moduleFolderActions: Action<'module'>[] = [
  {
    id: 'module.moveToFolder',
    element: 'module',
    label: 'Move to folder',
    icon: IconFolderSymlink,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      const target = ctx.target;
      if (!target) return;
      ctx.commands.openDialog({
        kind: 'moveToFolder',
        projectId: target.projectId,
        item: { kind: 'module', id: target.module.id, name: target.module.name },
      });
    },
  },
];

export const folderActions: Action<'folder'>[] = [
  {
    id: 'folder.newModule',
    element: 'folder',
    label: 'New module here',
    icon: IconPlus,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      const target = ctx.target;
      if (target) ctx.commands.openDialog({ kind: 'newModule', projectId: target.projectId, folderId: target.folder.folder });
    },
  },
  {
    id: 'folder.newModuleFromTemplate',
    element: 'folder',
    label: 'New module from template here',
    icon: IconTemplate,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      const target = ctx.target;
      if (target) {
        ctx.commands.openDialog({
          kind: 'newModule',
          projectId: target.projectId,
          folderId: target.folder.folder,
          fromTemplate: true,
        });
      }
    },
  },
  {
    id: 'folder.newSubfolder',
    element: 'folder',
    label: 'New subfolder',
    icon: IconFolderPlus,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      const target = ctx.target;
      if (target) {
        ctx.commands.openDialog({ kind: 'newModuleFolder', projectId: target.projectId, parentId: target.folder.folder });
      }
    },
  },
  {
    id: 'folder.rename',
    element: 'folder',
    label: 'Rename folder',
    icon: IconPencil,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      const target = ctx.target;
      if (!target) return;
      ctx.commands.openDialog({
        kind: 'renameModuleFolder',
        projectId: target.projectId,
        folderId: target.folder.folder,
        name: target.folder.name,
      });
    },
  },
  {
    id: 'folder.moveUp',
    element: 'folder',
    label: 'Move Up',
    icon: IconArrowUp,
    isAvailable: (ctx) => ctx.target !== null && ctx.target.position.index > 0,
    run: (ctx) => reorderFolder(ctx, 'up'),
  },
  {
    id: 'folder.moveDown',
    element: 'folder',
    label: 'Move Down',
    icon: IconArrowDown,
    isAvailable: (ctx) => ctx.target !== null && ctx.target.position.index < ctx.target.position.siblingCount - 1,
    run: (ctx) => reorderFolder(ctx, 'down'),
  },
  {
    id: 'folder.moveTo',
    element: 'folder',
    label: 'Move to folder',
    icon: IconFolderSymlink,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      const target = ctx.target;
      if (!target) return;
      ctx.commands.openDialog({
        kind: 'moveToFolder',
        projectId: target.projectId,
        item: { kind: 'folder', id: target.folder.folder, name: target.folder.name },
      });
    },
  },
  {
    // Only an empty folder can be deleted (the server refuses others); nothing else is removed.
    id: 'folder.delete',
    element: 'folder',
    label: 'Delete folder',
    icon: IconFolderX,
    color: 'red',
    isAvailable: (ctx) => ctx.target !== null && ctx.target.folder.children.length === 0,
    run: (ctx) => {
      const target = ctx.target;
      if (!target) return;
      ctx.commands.moduleFolders
        .remove(target.projectId, target.folder.folder)
        .then(() => notifications.show({ message: `Folder "${target.folder.name}" deleted`, color: 'green' }))
        .catch(notifyFailure('Failed to delete folder'));
    },
  },
];
