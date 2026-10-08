import { notifications } from '@mantine/notifications';
import { IconArrowDown, IconArrowUp, IconFileText, IconFolderOpen, IconFolderPlus, IconPlus, IconTemplate } from '@tabler/icons-react';
import { ApiError } from '../api/client';
import { isProjectStub, type Project } from '../api/types';
import type { Action, ActionContext } from './types';

/**
 * Actions on a project/subproject and on a module: the sidebar tree nodes'
 * menus, the menu bar and the project page. The target is the clicked node,
 * or the open project / module by default. Rename is not offered: there is no
 * rename API for projects or modules yet (add `project.rename` /
 * `module.rename` here once there is). Module folders, references, "open in
 * tab" and "detach" join these menus with their backlog items.
 */

type ProjectContext = ActionContext<'project'>;
type ModuleContext = ActionContext<'module'>;

/** The target project, if it can be loaded (stubs only open their error page). */
function loadedProject(ctx: ProjectContext): Project | null {
  const project = ctx.target;
  return project && !isProjectStub(project) ? project : null;
}

function reorderModule(ctx: ModuleContext, direction: 'up' | 'down') {
  const target = ctx.target;
  if (!target) return;
  ctx.commands.reorderModule(target.projectId, target.module.id, direction).catch((err) =>
    notifications.show({ message: err instanceof ApiError ? err.message : 'Failed to move module', color: 'red' }),
  );
}

export const projectActions: Action<'project'>[] = [
  {
    id: 'project.open',
    element: 'project',
    label: 'Open',
    icon: IconFolderOpen,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      if (ctx.target) ctx.commands.openProject(ctx.target.id);
    },
  },
  {
    // A new project whose parent is the target project (the project page's button).
    id: 'project.newSubproject',
    element: 'project',
    label: 'New subproject',
    icon: IconFolderPlus,
    opensDialog: true,
    isAvailable: (ctx) => loadedProject(ctx) !== null,
    run: (ctx) => {
      const project = loadedProject(ctx);
      if (project) ctx.commands.openDialog({ kind: 'newProject', parentProjectId: project.id });
    },
  },
  {
    id: 'project.newModule',
    element: 'project',
    label: 'New module',
    icon: IconPlus,
    opensDialog: true,
    isAvailable: (ctx) => loadedProject(ctx) !== null,
    run: (ctx) => {
      const project = loadedProject(ctx);
      if (project) ctx.commands.openDialog({ kind: 'newModule', projectId: project.id });
    },
  },
  {
    id: 'file.saveAsTemplate',
    element: 'project',
    label: 'Save as template',
    icon: IconTemplate,
    opensDialog: true,
    isAvailable: (ctx) => loadedProject(ctx) !== null,
    run: (ctx) => {
      const project = loadedProject(ctx);
      if (project) ctx.commands.openDialog({ kind: 'saveAsTemplate', project });
    },
  },
];

export const moduleActions: Action<'module'>[] = [
  {
    id: 'module.open',
    element: 'module',
    label: 'Open',
    icon: IconFileText,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      if (ctx.target) ctx.commands.openModule(ctx.target.projectId, ctx.target.module.id);
    },
  },
  {
    id: 'module.moveUp',
    element: 'module',
    label: 'Move Up',
    icon: IconArrowUp,
    isAvailable: (ctx) => {
      const target = ctx.target;
      return target !== null && target.moduleIds.indexOf(target.module.id) > 0;
    },
    run: (ctx) => reorderModule(ctx, 'up'),
  },
  {
    id: 'module.moveDown',
    element: 'module',
    label: 'Move Down',
    icon: IconArrowDown,
    isAvailable: (ctx) => {
      const target = ctx.target;
      if (!target) return false;
      const index = target.moduleIds.indexOf(target.module.id);
      return index >= 0 && index < target.moduleIds.length - 1;
    },
    run: (ctx) => reorderModule(ctx, 'down'),
  },
];
