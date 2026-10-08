import { IconFileExport, IconFileImport, IconTemplate } from '@tabler/icons-react';
import { appActions } from './appActions';
import { columnActions } from './columnActions';
import { linkActions } from './linkActions';
import { moduleActions, projectActions } from './projectActions';
import { purgeActions } from './purgeActions';
import { requirementActions } from './requirementActions';
import { matchesShortcut } from './shortcuts';
import type {
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
  ...purgeActions,
  ...requirementActions,
  ...projectActions,
  ...moduleActions,
  ...columnActions,
  ...linkActions,
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
          ? { projectId: state.projectId, module: state.module, moduleIds: state.modules.map((m) => m.id) }
          : null
      ) as ActionTargets[E];
    default:
      return null as ActionTargets[E];
  }
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
    if (bound && isSupported(action, ctx) && action.isAvailable(ctx)) return action;
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
      action('file.openFolder'),
      divider,
      {
        kind: 'submenu',
        label: 'Load template',
        icon: IconTemplate,
        entries: [action('file.newProjectFromTemplate'), action('file.importTemplate')],
      },
      action('file.saveAsTemplate'),
      action('project.enableHistory'),
      action('project.gitPush'),
      action('project.gitPull'),
      divider,
      {
        kind: 'submenu',
        label: 'Import',
        icon: IconFileImport,
        entries: [action('file.importCsv')],
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
      divider,
      action('requirement.delete'),
      // Destructive bulk deletes, kept apart from the requirement entries.
      divider,
      action('module.purge'),
      action('project.purge'),
    ],
  },
  {
    label: 'View',
    entries: [
      action('view.attributeView'),
      action('view.toggleDetailPanel'),
      divider,
      action('view.resetColumnWidths'),
      action('view.saveColumnWidths'),
      action('view.removeViewColumnWidths'),
      action('view.resetRowHeights'),
      divider,
      action('view.rowStyles'),
      action('view.toggleDarkMode'),
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
  action('view.saveColumnWidths'),
  action('view.removeViewColumnWidths'),
  action('view.resetRowHeights'),
];

/** Left click / ⋮ button of a column header: how the column is shown. */
export const COLUMN_HEADER_VIEW_MENU: MenuEntry[] = [
  action('column.wrapText'),
  action('column.autoFit'),
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
  divider,
  action('requirement.delete'),
];

/**
 * Left-click (navigation/view) menu of a requirement (its Id cell). "Open in
 * new tab" and "Detach" join it with the module tabs item.
 */
export const REQUIREMENT_NAVIGATION_MENU: MenuEntry[] = [
  action('requirement.showDetails'),
  action('requirement.showInTree'),
  action('requirement.goToLinked'),
];

/** Left click of a `>`/`<` link badge. */
export const LINK_NAVIGATION_MENU: MenuEntry[] = [action('link.goTo')];

/** Right click of a `>`/`<` link badge. */
export const LINK_CONTEXT_MENU: MenuEntry[] = [action('link.goTo'), divider, action('link.remove')];

/**
 * Right-click menu of a project / subproject node in the sidebar (left click
 * opens it). Rename, references, "open in tab" and "detach" join it with their
 * backlog items.
 */
export const PROJECT_NODE_MENU: MenuEntry[] = [
  action('project.open'),
  divider,
  action('project.newSubproject'),
  action('project.newModule'),
  divider,
  action('file.saveAsTemplate'),
];

/**
 * Right-click menu of a module node in the sidebar (left click opens it); the
 * module's project is passed as the `project` target for "New module".
 * Rename, folders, "open in tab" and "detach" join it with their items.
 */
export const MODULE_NODE_MENU: MenuEntry[] = [
  action('module.open'),
  divider,
  action('module.moveUp'),
  action('module.moveDown'),
  divider,
  action('project.newModule'),
];
