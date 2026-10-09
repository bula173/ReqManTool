import { IconFileExport, IconFileImport, IconTemplate } from '@tabler/icons-react';
import { findModuleLock } from '../api/moduleLocks';
import type { ModuleLock } from '../api/types';
import { appActions } from './appActions';
import { columnActions } from './columnActions';
import { treePosition } from '../components/moduleTree';
import { folderActions, moduleFolderActions, projectFolderActions } from './moduleFolderActions';
import { linkActions } from './linkActions';
import { appPromptActions, modulePromptActions, requirementPromptActions } from './promptActions';
import { tabKey } from '../layout/moduleTabs';
import { moduleActions, projectActions } from './projectActions';
import { providerActions } from './providerActions';
import { purgeActions } from './purgeActions';
import { pendingLinkActions, requirementActions } from './requirementActions';
import { matchesShortcut } from './shortcuts';
import { moduleWindowActions, requirementWindowActions, tabActions, windowActions } from './tabActions';
import type {
  AccessRequirement,
  Action,
  ActionContext,
  ActionTargetOverrides,
  ActionTargets,
  AppState,
  ElementType,
  MenuEntry,
} from './types';

/**
 * The single action registry: every action of every element type, by id.
 * The menu bar, the context menus, the keyboard shortcuts and toolbar
 * buttons all go through it (see `AppActionsProvider` / `useAppActions`).
 *
 * To support a new element type (sidebar node, column header, tab, …):
 * add it to `ActionTargets`, add its action list here, add a `resolveTarget`
 * case, and describe its context menu as a `MenuEntry[]` layout rendered
 * with `targets={{ <element>: clicked }}`.
 */
export const ACTIONS: Action[] = [
  ...appActions,
  ...appPromptActions,
  ...providerActions,
  ...requirementPromptActions,
  ...modulePromptActions,
  ...purgeActions,
  ...requirementActions,
  ...pendingLinkActions,
  ...projectActions,
  ...moduleActions,
  ...projectFolderActions,
  ...moduleFolderActions,
  ...folderActions,
  ...columnActions,
  ...linkActions,
  ...tabActions,
  ...moduleWindowActions,
  ...requirementWindowActions,
  ...windowActions,
] as Action[];

const ACTIONS_BY_ID = new Map<string, Action>(ACTIONS.map((action) => [action.id, action]));
if (ACTIONS_BY_ID.size !== ACTIONS.length) {
  throw new Error('Duplicate action id in the action registry');
}

export function getAction(id: string): Action {
  const action = ACTIONS_BY_ID.get(id);
  if (!action) throw new Error(`Unknown action '${id}'`);
  return action;
}

/**
 * What an action applies to when no element was clicked (menu bar,
 * shortcut, toolbar button): the selected requirement for requirement
 * actions, the open project / module for project / module actions. Columns
 * and links have no default: only their own menus target them.
 */
export function resolveTarget<E extends ElementType>(element: E, state: AppState): ActionTargets[E] {
  switch (element) {
    case 'requirement':
      return state.selectedRequirement as ActionTargets[E];
    case 'project':
      return state.project as ActionTargets[E];
    case 'module':
      return (
        state.projectId && state.module
          ? {
              projectId: state.projectId,
              module: state.module,
              moduleIds: state.modules.map((m) => m.id),
              position: treePosition(state.moduleTree, 'module', state.module.id),
            }
          : null
      ) as ActionTargets[E];
    case 'tab': {
      // The active tab: the open module, if it has a tab.
      const active =
        state.projectId && state.moduleId ? state.tabs.find((t) => tabKey(t) === `${state.projectId}/${state.moduleId}`) : undefined;
      return (active ?? null) as ActionTargets[E];
    }
    default:
      return null as ActionTargets[E];
  }
}

/**
 * The project an action's context applies to: the target project, the
 * project of the target module/folder/tab, else the open project.
 */
export function actionProjectId(ctx: ActionContext): string | null {
  switch (ctx.element) {
    case 'project':
      return (ctx.target as ActionTargets['project'])?.id ?? null;
    case 'module':
    case 'folder':
    case 'tab':
      return (ctx.target as { projectId: string } | null)?.projectId ?? null;
    default:
      return ctx.projectId;
  }
}

/** Whether a project is locked (`AppState.lockedProjectIds`). */
export function isProjectLocked(state: AppState, projectId: string | null | undefined): boolean {
  return !!projectId && state.lockedProjectIds.has(projectId);
}

/**
 * Whether the project's data cannot be changed here: it is locked, or the
 * user's role does not allow changes (server edition: viewer or no role).
 */
export function isProjectReadOnly(state: AppState, projectId: string | null | undefined): boolean {
  return isProjectLocked(state, projectId) || !state.access.canEdit(projectId);
}

/**
 * Server edition: the live edit lock of a module of the open project, or null
 * (local edition, no lock, or a module of another project: only the open
 * project's locks are loaded).
 */
export function moduleLockOf(
  state: AppState,
  projectId: string | null | undefined,
  moduleId: string | null | undefined,
): ModuleLock | null {
  if (!state.access.serverMode || !projectId || projectId !== state.projectId) return null;
  return findModuleLock(state.moduleLocks, moduleId);
}

/** Whether the module is checked out by another user (server edition). */
export function isModuleLockedByOther(
  state: AppState,
  projectId: string | null | undefined,
  moduleId: string | null | undefined,
): boolean {
  const lock = moduleLockOf(state, projectId, moduleId);
  return lock !== null && lock.user_id !== state.userId;
}

/** Whether the server requires a checkout before changes and nobody holds this module's (server edition). */
export function moduleNeedsCheckout(
  state: AppState,
  projectId: string | null | undefined,
  moduleId: string | null | undefined,
): boolean {
  return (
    !!moduleId &&
    projectId === state.projectId &&
    !!state.moduleLocks?.require_checkout &&
    moduleLockOf(state, projectId, moduleId) === null
  );
}

/**
 * Whether a module's content cannot be changed here: its project is
 * read-only, another user has the module checked out, or the server requires
 * a checkout that nobody holds. Always just `isProjectReadOnly` in the local edition.
 */
export function isModuleReadOnly(
  state: AppState,
  projectId: string | null | undefined,
  moduleId: string | null | undefined,
): boolean {
  if (isProjectReadOnly(state, projectId)) return true;
  if (!state.access.serverMode || !moduleId) return false;
  return isModuleLockedByOther(state, projectId, moduleId) || moduleNeedsCheckout(state, projectId, moduleId);
}

// Names of the requirement status attribute, compared lower-case with punctuation collapsed — the
// backend's `REPORT_ATTRIBUTE_ROLES["status"]` (the server decides; this only enables the cell).
const STATUS_ATTRIBUTE_NAMES = ['status', 'review status', 'approval status', 'requirement status', 'lifecycle status'];

function attributeKey(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^0-9a-z]+/g, ' ')
    .trim();
}

/** The id of the module's requirement status attribute (the RBC "Status" enum), or null. */
export function statusAttributeId(attributes: { id: string; name: string; type: string; system?: boolean }[]): string | null {
  const candidates = attributes.filter((a) => !a.system && (a.type === 'enum' || a.type === 'string'));
  for (const wanted of STATUS_ATTRIBUTE_NAMES) {
    const found = candidates.find((a) => attributeKey(a.name) === wanted || attributeKey(a.id) === wanted);
    if (found) return found.id;
  }
  return null;
}

/**
 * Whether the user may change requirement statuses in the module although its content is read-only
 * for them (server edition: a reviewer signs off). The project must not be locked and the module not
 * checked out by someone else. The server's status guards still decide each change.
 */
export function canChangeStatusOnly(
  state: AppState,
  projectId: string | null | undefined,
  moduleId: string | null | undefined,
): boolean {
  if (!state.access.serverMode || !projectId || isProjectLocked(state, projectId)) return false;
  if (!state.access.canReview(projectId)) return false;
  return !isModuleLockedByOther(state, projectId, moduleId) && !moduleNeedsCheckout(state, projectId, moduleId);
}

/** The module whose content an action changes: the target module/tab, a link's source module, else the open module. */
function actionModuleId(ctx: ActionContext): string | null {
  switch (ctx.element) {
    case 'module':
      return (ctx.target as ActionTargets['module'])?.module.id ?? null;
    case 'tab':
      return (ctx.target as ActionTargets['tab'])?.moduleId ?? null;
    case 'link': {
      const link = ctx.target as ActionTargets['link'];
      const config = link ? ctx.project?.link_configurations.find((c) => c.id === link.configId) : undefined;
      return config?.source_module_id ?? ctx.moduleId;
    }
    default:
      return ctx.moduleId;
  }
}

function editsModuleContent(action: Action): boolean {
  return action.editsModule ?? (action.element === 'requirement' || action.element === 'link');
}

function meets(state: AppState, requirement: AccessRequirement, projectId: string | null): boolean {
  switch (requirement) {
    case 'projectEditor':
      return state.access.canEdit(projectId);
    case 'projectAdmin':
      return state.access.canAdmin(projectId);
    case 'createProjects':
      return state.access.canCreateProjects;
    case 'connections':
      return state.access.canUseConnections;
    case 'serverAdmin':
      return state.access.serverAdmin;
  }
}

/**
 * Whether an action can run: its own `isAvailable`; for an action that
 * `edits`, its project is not read-only (locked, or the user's role does
 * not allow changes) and, when it changes a module's content
 * (`editsModule`), that module is not checked out by someone else (server
 * edition); and the permissions it `requires`. Every menu,
 * shortcut and button goes through this (never `action.isAvailable` alone).
 */
export function isActionAvailable<E extends ElementType>(action: Action<E>, ctx: ActionContext<E>): boolean {
  if (action.edits || action.requires) {
    const projectId = actionProjectId(ctx as ActionContext);
    if (action.edits && isProjectReadOnly(ctx, projectId)) return false;
    if (
      action.edits &&
      ctx.access.serverMode &&
      editsModuleContent(action as Action) &&
      isModuleReadOnly(ctx, projectId, actionModuleId(ctx as ActionContext))
    ) {
      return false;
    }
    if (action.requires && !action.requires.every((r) => meets(ctx, r, projectId))) return false;
  }
  return action.isAvailable(ctx);
}

/** Whether the target supports the action (see `Action.isSupported`); unsupported ones are hidden and never run. */
export function isSupported<E extends ElementType>(action: Action<E>, ctx: ActionContext<E>): boolean {
  return action.isSupported ? action.isSupported(ctx) : true;
}

/**
 * The context to run `action` with. `targets` holds the clicked element(s)
 * of a context menu keyed by element type; the action receives the entry of
 * its own element type, or its default target when there is none.
 */
export function contextFor<E extends ElementType>(
  action: Action<E>,
  state: AppState,
  targets?: ActionTargetOverrides,
): ActionContext<E> {
  const explicit = targets?.[action.element] as ActionTargets[E] | undefined;
  const target = explicit !== undefined ? explicit : resolveTarget(action.element, state);
  return { ...state, element: action.element, target };
}

/**
 * The action a key press triggers: the first registered action with a
 * matching shortcut that is available in the current state. Several actions
 * may share a key; registry order decides. A `whenNoTarget` binding only
 * counts while the action's default target is empty: Ctrl/Cmd+V pastes as a
 * child of the selected requirement, or at the root only when nothing is
 * selected (with a selection it never falls through to the root).
 */
export function shortcutAction(event: KeyboardEvent, state: AppState): Action | null {
  for (const action of ACTIONS) {
    const ctx = contextFor(action, state);
    const bound = action.shortcuts?.some(
      (shortcut) => matchesShortcut(event, shortcut) && !(shortcut.whenNoTarget && ctx.target !== null),
    );
    if (bound && isSupported(action, ctx) && isActionAvailable(action, ctx)) return action;
  }
  return null;
}

/** Actions with shortcuts, for Help ▸ Keyboard shortcuts. */
export function actionsWithShortcuts(): Action[] {
  return ACTIONS.filter((action) => action.shortcuts && action.shortcuts.length > 0);
}

// --- menu layouts --------------------------------------------------------------

const action = (id: string): MenuEntry => ({ kind: 'action', id });
const divider: MenuEntry = { kind: 'divider' };

/** The application menu bar. */
export const MENU_BAR: { label: string; entries: MenuEntry[] }[] = [
  {
    label: 'File',
    entries: [
      action('file.newProject'),
      action('file.addProjectFromRemote'),
      action('file.addProjectFromProvider'),
      action('file.openFolder'),
      divider,
      {
        kind: 'submenu',
        label: 'Load template',
        icon: IconTemplate,
        entries: [
          action('file.newProjectFromTemplate'),
          action('file.importTemplate'),
          divider,
          action('project.newModuleFromTemplate'),
        ],
      },
      action('file.saveAsTemplate'),
      action('module.saveAsTemplate'),
      action('project.enableHistory'),
      action('project.gitPush'),
      action('project.gitPull'),
      action('project.publish'),
      action('project.requestReview'),
      divider,
      {
        kind: 'submenu',
        label: 'Import',
        icon: IconFileImport,
        entries: [action('file.importCsv'), action('module.importTestResults')],
      },
      {
        kind: 'submenu',
        label: 'Export',
        icon: IconFileExport,
        entries: [
          action('file.export.reqif'),
          action('file.export.csv'),
          action('file.export.xlsx'),
          action('file.export.docx'),
        ],
      },
    ],
  },
  {
    label: 'Edit',
    entries: [
      action('requirement.cut'),
      action('requirement.copy'),
      action('requirement.pasteAsChild'),
      action('requirement.pasteAsSibling'),
      action('requirement.pasteAtRoot'),
      divider,
      action('requirement.addTopLevel'),
      action('requirement.addChild'),
      action('requirement.addSibling'),
      divider,
      action('requirement.moveUp'),
      action('requirement.moveDown'),
      action('requirement.moveTo'),
      divider,
      action('requirement.link'),
      action('requirement.startLink'),
      action('requirement.makeLink'),
      action('edit.cancelLink'),
      divider,
      action('module.applySkeleton'),
      divider,
      action('requirement.delete'),
      // Destructive bulk deletes, kept apart from the requirement entries.
      divider,
      action('module.purge'),
      action('project.purge'),
      divider,
      action('project.rename'),
      action('project.lock'),
      action('project.unlock'),
      action('project.remove'),
      divider,
      // Server edition only: the open module's edit lock.
      action('module.checkOut'),
      action('module.release'),
      action('module.breakLock'),
    ],
  },
  {
    label: 'View',
    entries: [
      action('view.attributeView'),
      action('view.edit'),
      action('view.verificationColumn'),
      action('view.toggleLeftPanel'),
      action('view.toggleDetailPanel'),
      divider,
      action('tab.close'),
      action('tab.detach'),
      divider,
      action('view.resetColumnWidths'),
      action('view.resetColumnOrder'),
      action('view.saveLayout'),
      action('view.revertLayout'),
      action('view.saveLayoutAsNewView'),
      action('view.removeViewColumnWidths'),
      action('view.resetRowHeights'),
      divider,
      action('view.rowStyles'),
      action('view.toggleDarkMode'),
      divider,
      // Commit browser (read-only) of the open project / module / selected requirement.
      action('project.commits'),
      action('module.commits'),
      action('requirement.commits'),
    ],
  },
  {
    // The AI prompt library (no AI service is called: copy, paste into a chat, paste the answer back).
    label: 'Tools',
    entries: [
      action('tools.aiPrompts'),
      action('tools.pasteAiResult'),
      divider,
      action('tools.providerConnections'),
    ],
  },
  {
    // Read-only reports; the module ones apply to the open module.
    label: 'Reports',
    entries: [
      action('report.traceability'),
      divider,
      action('module.verificationReport'),
      action('module.executionReport'),
    ],
  },
  {
    label: 'Help',
    entries: [action('help.shortcuts'), action('help.documentation'), divider, action('help.about')],
  },
];

// Context menus: one layout per element type and click kind. Left click
// opens the navigation/view menu, right click (Shift+F10, the menu key) the
// editing menu. Entries a place does not support are left out of its layout
// (or hidden through `isSupported`); unavailable ones are disabled.

/** The table-wide part of a requirements table column header menu. */
const TABLE_LAYOUT_ENTRIES: MenuEntry[] = [
  action('view.resetColumnWidths'),
  action('view.resetColumnOrder'),
  action('view.saveLayout'),
  action('view.revertLayout'),
  action('view.saveLayoutAsNewView'),
  action('view.edit'),
  action('view.removeViewColumnWidths'),
  action('view.resetRowHeights'),
];

/** Left click / ⋮ button of a column header: how the column is shown. */
export const COLUMN_HEADER_VIEW_MENU: MenuEntry[] = [
  action('column.wrapText'),
  action('column.autoFit'),
  action('column.moveLeft'),
  action('column.moveRight'),
  divider,
  action('view.verificationColumn'),
  divider,
  ...TABLE_LAYOUT_ENTRIES,
];

/** Right click of a column header: the attribute's properties, then the view entries. */
export const COLUMN_HEADER_EDIT_MENU: MenuEntry[] = [
  action('column.attributeModules'),
  divider,
  ...COLUMN_HEADER_VIEW_MENU,
];

/** Right-click (editing) menu of a requirement row, outline node or table cell. */
export const REQUIREMENT_CONTEXT_MENU: MenuEntry[] = [
  action('requirement.cut'),
  action('requirement.copy'),
  action('requirement.pasteAsChild'),
  action('requirement.pasteAsSibling'),
  action('requirement.pasteAtRoot'),
  divider,
  action('requirement.addChild'),
  action('requirement.addSibling'),
  divider,
  action('requirement.moveUp'),
  action('requirement.moveDown'),
  action('requirement.moveTo'),
  divider,
  action('requirement.link'),
  action('requirement.startLink'),
  action('requirement.makeLink'),
  action('edit.cancelLink'),
  divider,
  action('requirement.aiPrompts'),
  action('requirement.commits'),
  divider,
  action('requirement.delete'),
];

/**
 * Left-click (navigation/view) menu of a requirement (its Id cell). "Open in
 * new window" shows the requirement's module in a detached window.
 */
export const REQUIREMENT_NAVIGATION_MENU: MenuEntry[] = [
  action('requirement.showDetails'),
  action('requirement.showInTree'),
  action('requirement.goToLinked'),
  divider,
  action('requirement.openInWindow'),
  divider,
  action('requirement.commits'),
];

/** Right click of a module tab in the tab bar (left click switches to it). */
export const MODULE_TAB_MENU: MenuEntry[] = [
  action('tab.close'),
  action('tab.closeOthers'),
  action('tab.closeToRight'),
  divider,
  action('tab.moveLeft'),
  action('tab.moveRight'),
  divider,
  action('tab.detach'),
];

/** Left click of a `>`/`<` link badge. */
export const LINK_NAVIGATION_MENU: MenuEntry[] = [action('link.goTo')];

/** Right click of a `>`/`<` link badge. */
export const LINK_CONTEXT_MENU: MenuEntry[] = [action('link.goTo'), divider, action('link.remove')];

/**
 * Right-click menu of a project / subproject node in the sidebar (left click
 * opens it).
 */
export const PROJECT_NODE_MENU: MenuEntry[] = [
  action('project.open'),
  divider,
  action('project.rename'),
  action('project.newSubproject'),
  action('project.newModule'),
  action('project.newModuleFromTemplate'),
  action('project.newFolder'),
  divider,
  action('project.references'),
  divider,
  action('report.traceability'),
  action('project.commits'),
  divider,
  action('file.saveAsTemplate'),
  divider,
  action('project.lock'),
  action('project.unlock'),
  action('project.remove'),
];

/**
 * Right-click menu of a module node in the sidebar (left click opens it); the
 * module's project is passed as the `project` target for "New module".
 * Move Up/Down move it among the entries of its folder. Rename joins it with
 * its backlog item.
 */
export const MODULE_NODE_MENU: MenuEntry[] = [
  action('module.open'),
  action('module.openInNewTab'),
  action('module.openInWindow'),
  divider,
  action('module.moveUp'),
  action('module.moveDown'),
  action('module.moveToFolder'),
  divider,
  action('module.verificationReport'),
  action('module.executionReport'),
  action('module.importTestResults'),
  action('module.commits'),
  divider,
  // Server edition only (hidden in the local edition).
  action('module.checkOut'),
  action('module.release'),
  action('module.breakLock'),
  divider,
  action('module.aiPrompts'),
  divider,
  action('module.applySkeleton'),
  action('module.saveAsTemplate'),
  divider,
  action('project.newModule'),
  action('project.newModuleFromTemplate'),
];

/**
 * Right-click menu of a module folder node in the sidebar (left click expands
 * or collapses it). Folders are purely organisational: none of these change a
 * module id, a requirement or a link.
 */
export const FOLDER_NODE_MENU: MenuEntry[] = [
  action('folder.newModule'),
  action('folder.newModuleFromTemplate'),
  action('folder.newSubfolder'),
  divider,
  action('folder.rename'),
  action('folder.moveUp'),
  action('folder.moveDown'),
  action('folder.moveTo'),
  divider,
  action('folder.delete'),
];
