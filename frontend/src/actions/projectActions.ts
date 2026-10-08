import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import {
  IconArrowDown,
  IconArrowUp,
  IconChecklist,
  IconFileImport,
  IconFileText,
  IconFolderOpen,
  IconFolderPlus,
  IconGitCommit,
  IconGridDots,
  IconLink,
  IconListTree,
  IconLock,
  IconLockAccess,
  IconLockOpen,
  IconLockOff,
  IconPlus,
  IconReportAnalytics,
  IconTemplate,
  IconTrashX,
} from '@tabler/icons-react';
import { ApiError } from '../api/client';
import { findModuleLock } from '../api/moduleLocks';
import { moduleTypeOf } from '../api/moduleTypes';
import { isProjectStub, type Project } from '../api/types';
import { commitBrowserPath } from '../components/commits';
import type { Action, ActionContext } from './types';

/**
 * Actions on a project/subproject and on a module: the sidebar tree nodes'
 * menus, the menu bar and the project page. The target is the clicked node,
 * or the open project / module by default. Rename is not offered: there is no
 * rename API for projects or modules yet (add `project.rename` /
 * `module.rename` here once there is). Module folder actions are in
 * `moduleFolderActions.ts`; "Open in new tab" / "Open in new window" are in
 * `tabActions.ts`.
 */

type ProjectContext = ActionContext<'project'>;
type ModuleContext = ActionContext<'module'>;

/** The target project, if it can be loaded (stubs only open their error page). */
function loadedProject(ctx: ProjectContext): Project | null {
  const project = ctx.target;
  return project && !isProjectStub(project) ? project : null;
}

/** The target module's live edit lock (server edition; open project only), or null. */
function targetLock(ctx: ModuleContext) {
  const target = ctx.target;
  if (!target || !ctx.access.serverMode || target.projectId !== ctx.projectId) return null;
  return findModuleLock(ctx.moduleLocks, target.module.id);
}

const serverEditionOnly = (ctx: ModuleContext) => ctx.access.serverMode;

function notifyLockError(err: unknown, fallback: string) {
  notifications.show({ message: err instanceof ApiError ? err.message : fallback, color: 'red' });
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
    requires: ['createProjects', 'projectEditor'],
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
    edits: true,
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
    // The module template list is fetched by the dialog when it opens (never before).
    id: 'project.newModuleFromTemplate',
    element: 'project',
    edits: true,
    label: 'New module from template',
    icon: IconTemplate,
    opensDialog: true,
    isAvailable: (ctx) => loadedProject(ctx) !== null,
    run: (ctx) => {
      const project = loadedProject(ctx);
      if (project) ctx.commands.openDialog({ kind: 'newModule', projectId: project.id, fromTemplate: true });
    },
  },
  {
    // The projects this one references (cross-project link targets): sidebar menu and project page.
    id: 'project.references',
    element: 'project',
    edits: true,
    label: 'References',
    icon: IconLink,
    opensDialog: true,
    isAvailable: (ctx) => loadedProject(ctx) !== null,
    run: (ctx) => {
      const project = loadedProject(ctx);
      if (project) ctx.commands.openDialog({ kind: 'projectReferences', project });
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
  {
    // Reports ▸ Traceability matrix (read-only report page of the target project).
    id: 'report.traceability',
    element: 'project',
    label: 'Traceability matrix',
    icon: IconGridDots,
    isAvailable: (ctx) => loadedProject(ctx) !== null,
    run: (ctx) => {
      const project = loadedProject(ctx);
      if (project) ctx.commands.navigate(`/projects/${encodeURIComponent(project.id)}/reports/traceability`);
    },
  },
  {
    // Lock: every change of the project is refused (server-side) until unlocked; the dialog asks for a reason.
    id: 'project.lock',
    requires: ['projectAdmin'],
    element: 'project',
    label: 'Lock project',
    icon: IconLock,
    opensDialog: true,
    isAvailable: (ctx) => {
      const project = loadedProject(ctx);
      return project !== null && !ctx.lockedProjectIds.has(project.id);
    },
    run: (ctx) => {
      const project = loadedProject(ctx);
      if (project) ctx.commands.openDialog({ kind: 'lockProject', project });
    },
  },
  {
    id: 'project.unlock',
    requires: ['projectAdmin'],
    element: 'project',
    label: 'Unlock project',
    icon: IconLockOpen,
    isAvailable: (ctx) => {
      const project = loadedProject(ctx);
      return project !== null && ctx.lockedProjectIds.has(project.id);
    },
    run: (ctx) => {
      const project = loadedProject(ctx);
      if (!project) return;
      ctx.commands.unlockProject(project.id).then(
        () => notifications.show({ message: `Project "${project.name}" unlocked`, color: 'green' }),
        (err) =>
          notifications.show({ message: err instanceof ApiError ? err.message : 'Failed to unlock project', color: 'red' }),
      );
    },
  },
  {
    // Deletes the project folder after a typed-name confirmation; refused while locked, with
    // subprojects or while referenced (the dialog lists why). `edits`: unavailable while locked.
    id: 'project.remove',
    requires: ['projectAdmin'],
    element: 'project',
    label: 'Remove project',
    icon: IconTrashX,
    color: 'red',
    opensDialog: true,
    edits: true,
    isAvailable: (ctx) => loadedProject(ctx) !== null,
    run: (ctx) => {
      const project = loadedProject(ctx);
      if (!project) return;
      ctx.commands.openDialog({
        kind: 'removeProject',
        projectId: project.id,
        projectName: project.name,
        dataDir: ctx.dataDir,
      });
    },
  },
  {
    // The commit browser at project scope (read-only; the page explains a project without history).
    id: 'project.commits',
    element: 'project',
    label: 'Project commits',
    icon: IconGitCommit,
    isAvailable: (ctx) => loadedProject(ctx) !== null,
    run: (ctx) => {
      const project = loadedProject(ctx);
      if (project) ctx.commands.navigate(commitBrowserPath(project.id));
    },
  },
];

/** The path of a module report page (see `App.tsx`). */
function moduleReportPath(target: NonNullable<ModuleContext['target']>, report: 'verification' | 'execution'): string {
  return `/projects/${encodeURIComponent(target.projectId)}/reports/${report}/${encodeURIComponent(target.module.id)}`;
}

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
    edits: true,
    label: 'Move Up',
    icon: IconArrowUp,
    isAvailable: (ctx) => {
      const target = ctx.target;
      return target !== null && target.position !== null && target.position.index > 0;
    },
    run: (ctx) => reorderModule(ctx, 'up'),
  },
  {
    id: 'module.moveDown',
    element: 'module',
    edits: true,
    label: 'Move Down',
    icon: IconArrowDown,
    isAvailable: (ctx) => {
      const target = ctx.target;
      if (!target) return false;
      return target.position !== null && target.position.index < target.position.siblingCount - 1;
    },
    run: (ctx) => reorderModule(ctx, 'down'),
  },
  {
    // Reports ▸ Verification report: only for a requirements module (disabled otherwise).
    id: 'module.verificationReport',
    element: 'module',
    label: 'Verification report',
    icon: IconReportAnalytics,
    isAvailable: (ctx) => ctx.target !== null && moduleTypeOf(ctx.target.module) === 'requirements',
    run: (ctx) => {
      if (ctx.target) ctx.commands.navigate(moduleReportPath(ctx.target, 'verification'));
    },
  },
  {
    // Reports ▸ Test execution report: only for a test_execution module.
    id: 'module.executionReport',
    element: 'module',
    label: 'Test execution report',
    icon: IconChecklist,
    isAvailable: (ctx) => ctx.target !== null && moduleTypeOf(ctx.target.module) === 'test_execution',
    run: (ctx) => {
      if (ctx.target) ctx.commands.navigate(moduleReportPath(ctx.target, 'execution'));
    },
  },
  {
    // JUnit XML / Robot output.xml results as execution records: only for a test_execution module.
    // Also File ▸ Import ▸ Test results (the open module).
    id: 'module.importTestResults',
    element: 'module',
    edits: true,
    editsModule: true,
    label: 'Import test results',
    icon: IconFileImport,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null && moduleTypeOf(ctx.target.module) === 'test_execution',
    run: (ctx) => {
      const target = ctx.target;
      if (!target) return;
      ctx.commands.openDialog({
        kind: 'importTestResults',
        projectId: target.projectId,
        moduleId: target.module.id,
        moduleName: target.module.name,
      });
    },
  },
  {
    // Adds a chapter template's missing headings to the module (previewed first; nothing existing changes).
    id: 'module.applySkeleton',
    element: 'module',
    edits: true,
    editsModule: true,
    label: 'Apply chapter template…',
    icon: IconListTree,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      const target = ctx.target;
      if (!target) return;
      ctx.commands.openDialog({
        kind: 'applySkeleton',
        projectId: target.projectId,
        moduleId: target.module.id,
        moduleName: target.module.name,
        moduleType: moduleTypeOf(target.module),
      });
    },
  },
  {
    // The commit browser limited to the module's folder.
    id: 'module.commits',
    element: 'module',
    label: 'Module commits',
    icon: IconGitCommit,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      if (ctx.target) ctx.commands.navigate(commitBrowserPath(ctx.target.projectId, ctx.target.module.id));
    },
  },
  {
    // Server edition: check the module out for exclusive editing (optional reason, in a dialog).
    // Everybody else gets it read-only until it is released, broken or expires.
    id: 'module.checkOut',
    element: 'module',
    requires: ['projectEditor'],
    label: 'Check out for editing',
    icon: IconLockAccess,
    opensDialog: true,
    isSupported: serverEditionOnly,
    isAvailable: (ctx) => ctx.target !== null && targetLock(ctx) === null,
    run: (ctx) => {
      const target = ctx.target;
      if (target) ctx.commands.openDialog({ kind: 'checkOutModule', projectId: target.projectId, module: target.module });
    },
  },
  {
    // Server edition: give one's own checkout back.
    id: 'module.release',
    element: 'module',
    label: 'Release module',
    icon: IconLockOpen,
    isSupported: serverEditionOnly,
    isAvailable: (ctx) => {
      const lock = targetLock(ctx);
      return lock !== null && lock.user_id === ctx.userId;
    },
    run: (ctx) => {
      const target = ctx.target;
      if (!target) return;
      ctx.commands.releaseModule(target.projectId, target.module.id).then(
        () => notifications.show({ message: `Module "${target.module.name}" released`, color: 'green' }),
        (err) => notifyLockError(err, 'Failed to release the module'),
      );
    },
  },
  {
    // Server edition: a project admin (or server admin) takes someone else's checkout away.
    id: 'module.breakLock',
    element: 'module',
    requires: ['projectAdmin'],
    label: 'Break lock',
    icon: IconLockOff,
    color: 'red',
    opensDialog: true,
    isSupported: serverEditionOnly,
    isAvailable: (ctx) => {
      const lock = targetLock(ctx);
      return lock !== null && lock.user_id !== ctx.userId;
    },
    run: (ctx) => {
      const target = ctx.target;
      const lock = targetLock(ctx);
      if (!target || !lock) return;
      const { commands } = ctx;
      modals.openConfirmModal({
        title: 'Break lock',
        children: `Module "${target.module.name}" is checked out for editing by ${lock.user_name}. Break the lock? Changes they have not saved yet may then be refused or conflict.`,
        labels: { confirm: 'Break lock', cancel: 'Cancel' },
        confirmProps: { color: 'red' },
        onConfirm: () => {
          commands.breakModuleLock(target.projectId, target.module.id).then(
            () => notifications.show({ message: `Lock of module "${target.module.name}" broken`, color: 'green' }),
            (err) => notifyLockError(err, 'Failed to break the lock'),
          );
        },
      });
    },
  },
  {
    // Attributes that apply to the module, an optional view and its headings, as a user module template.
    id: 'module.saveAsTemplate',
    element: 'module',
    label: 'Save module as template',
    icon: IconTemplate,
    opensDialog: true,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => {
      const target = ctx.target;
      if (!target) return;
      ctx.commands.openDialog({
        kind: 'saveModuleAsTemplate',
        projectId: target.projectId,
        moduleId: target.module.id,
        moduleName: target.module.name,
      });
    },
  },
];
