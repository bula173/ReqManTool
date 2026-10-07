import type { ComponentType } from 'react';
import type {
  GitPullResult,
  GitPushResult,
  GitStatus,
  Module,
  Project,
  Requirement,
  TemplateSummary,
  View,
} from '../api/types';

/**
 * The shared action registry's types.
 *
 * Every user command that can be reached from more than one place (the menu
 * bar, a context menu, a keyboard shortcut, a toolbar button) is an
 * `Action`: an id, a label, an optional shortcut, `isAvailable(ctx)` and
 * `run(ctx)`. Callers never re-implement an action; they look it up by id
 * and run it with an `ActionContext` built by `AppActionsProvider`.
 *
 * Actions are grouped by the element type they apply to. The element type
 * decides what `ctx.target` is: nothing for application-wide actions, a
 * requirement for requirement actions. New element types (sidebar tree
 * nodes, column headers, module tabs, …) are added to `ActionTargets`, get
 * their own action list, and their context menus pass the clicked element
 * keyed by its type (`ActionTargetOverrides`, e.g. `{ requirement: row }`) —
 * the menu bar and shortcuts keep working unchanged.
 */
export interface ActionTargets {
  /** Application-wide (File/View/Help) actions: no target. */
  app: null;
  /**
   * A requirement of the open module: the right-clicked row in a context
   * menu, the selected requirement in the menu bar and for shortcuts.
   */
  requirement: Requirement | null;
}

export type ElementType = keyof ActionTargets;

/** A keyboard shortcut. `mod` is Cmd on macOS and Ctrl elsewhere. */
export interface Shortcut {
  /** `KeyboardEvent.key`, compared case-insensitively (e.g. `'c'`, `'ArrowUp'`). */
  key: string;
  mod?: boolean;
  shift?: boolean;
  alt?: boolean;
  /**
   * The binding only triggers the action when its default target is empty
   * (e.g. no requirement selected). Used for fallbacks such as Ctrl/Cmd+V
   * pasting at the module root only when nothing is selected.
   */
  whenNoTarget?: boolean;
}

/**
 * The requirement clipboard: one requirement (with its subtree), scoped to
 * the module it was taken from. `AppActionsProvider` also scopes it to the
 * data folder: switching folders (here or in another client) empties it.
 */
export interface RequirementClipboard {
  mode: 'copy' | 'cut';
  projectId: string;
  moduleId: string;
  requirement: Requirement;
}

/** Dialogs hosted once by the app shell, opened by actions and buttons. */
export type DialogRequest =
  | { kind: 'newProject'; parentProjectId?: string | null; templateId?: string }
  | { kind: 'addProjectFromRemote' }
  | { kind: 'dataDir' }
  | { kind: 'saveAsTemplate'; project: Project }
  | { kind: 'rowStyles'; project: Project }
  | { kind: 'importTemplate' }
  | { kind: 'importCsv'; projectId: string; moduleId: string; moduleName: string }
  | { kind: 'shortcuts' }
  | { kind: 'about' };

export type ExportFormat = 'reqif' | 'csv' | 'xlsx' | 'docx';

/** Capabilities actions use to do their work; implemented once by `AppActionsProvider`. */
export interface AppCommands {
  navigate: (path: string) => void;
  openDialog: (request: DialogRequest) => void;
  setClipboard: (entry: RequirementClipboard | null) => void;
  selectRequirement: (requirementId: string | null) => void;
  setActiveView: (viewId: string | null) => void;
  /** Store (or, with null, remove) a view's shared column widths in project.json. */
  setViewColumnWidths: (viewId: string, widths: Record<string, number> | null) => Promise<View>;
  setDetailPanelOpen: (open: boolean) => void;
  setColorScheme: (scheme: 'light' | 'dark') => void;
  /** "Enable history (git)" for the open project; resolves with its new git status. */
  enableHistory: () => Promise<GitStatus>;
  /** Push / Pull the open project's git remote (explicit sync; see the Git panel). */
  gitPush: () => Promise<GitPushResult>;
  gitPull: () => Promise<GitPullResult>;
  /** Starts a file download (export endpoints answer with Content-Disposition: attachment). */
  download: (url: string) => void;
  openExternal: (url: string) => void;
  /** Hard-delete every requirement of a module / of every module of a project; resolve with the count. */
  purgeModule: (projectId: string, moduleId: string) => Promise<{ purged: number }>;
  purgeProject: (projectId: string) => Promise<{ purged: number }>;
  /** Requirement mutations of the open module (TanStack mutations; invalidate their queries). */
  requirements: {
    copy: (requirementId: string, targetParentId: string | null) => Promise<Requirement>;
    move: (requirementId: string, parentId: string | null) => Promise<Requirement>;
    reorder: (requirementId: string, direction: 'up' | 'down') => Promise<Requirement>;
    remove: (requirementId: string) => Promise<unknown>;
  };
}

/** Application state every action can read. Values are null/empty when the route does not provide them. */
export interface AppState {
  projectId: string | null;
  moduleId: string | null;
  project: Project | null;
  /** Set on a module route once the module has loaded. */
  module: Module | null;
  /** The open module's requirements (empty outside a module route or while loading). */
  requirements: Requirement[];
  selectedRequirement: Requirement | null;
  clipboard: RequirementClipboard | null;
  views: View[];
  /** The open project's history (git) status, once loaded. */
  gitStatus: GitStatus | null;
  activeViewId: string | null;
  detailPanelOpen: boolean;
  colorScheme: 'light' | 'dark';
  templates: { data: TemplateSummary[]; isLoading: boolean; isError: boolean };
  commands: AppCommands;
}

/**
 * Explicit targets of a context menu or button, keyed by element type: each
 * action gets the entry of its own element type, so one menu may mix element
 * types. Missing entries fall back to the default target (`resolveTarget`).
 */
export type ActionTargetOverrides = Partial<ActionTargets>;

export interface ActionContext<E extends ElementType = ElementType> extends AppState {
  element: E;
  target: ActionTargets[E];
}

/** One generated entry of an action's submenu (e.g. one per view or per template). */
export interface DynamicMenuItem {
  key: string;
  label: string;
  checked?: boolean;
  disabled?: boolean;
  /** Shown as the item's tooltip/description (e.g. why a template cannot be used). */
  description?: string;
  run?: () => void;
}

export type ActionIcon = ComponentType<{ size?: number | string }>;

export interface Action<E extends ElementType = ElementType> {
  /** Unique, prefixed by the element type's area, e.g. `requirement.copy`, `file.newProject`. */
  id: string;
  element: E;
  label: string;
  icon?: ActionIcon;
  color?: string;
  /** The first shortcut is the one displayed; the others are alternatives (see `shortcutAction`). */
  shortcuts?: Shortcut[];
  /** Disabled (menus) / not triggered (shortcuts) when false. */
  isAvailable(ctx: ActionContext<E>): boolean;
  /** Toggle actions: shows a check mark. */
  isChecked?(ctx: ActionContext<E>): boolean;
  /** Ends with an ellipsis in menus when the action opens a dialog. */
  opensDialog?: boolean;
  run(ctx: ActionContext<E>): void;
  /** Present for actions that are a list of choices: rendered as a submenu instead of an item. */
  items?(ctx: ActionContext<E>): DynamicMenuItem[];
}

/** A menu layout: which actions appear where. The same registry action can appear in several menus. */
export type MenuEntry =
  | { kind: 'action'; id: string }
  | { kind: 'divider' }
  | { kind: 'submenu'; label: string; icon?: ActionIcon; entries: MenuEntry[] };
