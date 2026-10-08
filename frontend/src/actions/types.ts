import type { ComponentType } from 'react';
import type {
  AttributeDefinition,
  GitPullResult,
  GitPushResult,
  GitStatus,
  Module,
  Project,
  ProjectListEntry,
  Requirement,
  TemplateSummary,
  View,
} from '../api/types';
import type { ResizableColumns } from '../hooks/columnLayout';

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
  /**
   * A project or subproject: a sidebar tree node (possibly a stub that cannot
   * be loaded), or the open project in the menu bar and on the project page.
   */
  project: ProjectListEntry | null;
  /** A module: a sidebar module node, or the open module by default. */
  module: ModuleTarget | null;
  /** A requirements table column header (no default: only its own menus). */
  column: ColumnTarget | null;
  /** One `>`/`<` link badge of a requirement (no default). */
  link: LinkTarget | null;
  // Hooks for later items: module tabs (`tab`: close, close others, detach,
  // split view), module folders (`folder`) and project references
  // (`reference`) become element types here once those features exist; their
  // menus reuse `ActionMenuEntries` and `AppActionsValue.openMenu` unchanged.
}

/** A module with the order of its project's modules (for Move Up/Down availability). */
export interface ModuleTarget {
  projectId: string;
  module: Module;
  /** The project's module ids in display order. */
  moduleIds: string[];
}

/** A requirements table column: the Id column (`attribute` null) or an attribute column. */
export interface ColumnTarget {
  columnKey: string;
  label: string;
  attribute: AttributeDefinition | null;
  /** The table's column layout controls (width, wrap). */
  columns: ResizableColumns;
}

/**
 * A link shown as a badge in a requirement's link attribute: the attribute id
 * is the link configuration id; `>` is an outgoing link (the requirement is
 * the source), `<` an incoming one (the requirement is the target).
 */
export interface LinkTarget {
  requirement: Requirement;
  configId: string;
  direction: 'out' | 'in';
  /** The requirement at the other end of the link. */
  otherId: string;
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
  | { kind: 'newModule'; projectId: string }
  /** New requirement under `parentId` (null: top level); with `insertIndex`, then placed at that sibling position. */
  | { kind: 'addRequirement'; projectId: string; moduleId: string; parentId: string | null; insertIndex?: number }
  | { kind: 'linkRequirement'; projectId: string; moduleId: string; source: Requirement }
  | { kind: 'moveRequirement'; projectId: string; moduleId: string; source: Requirement }
  | { kind: 'attributeModules'; projectId: string; attribute: AttributeDefinition }
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
    /** `index`: 0-based position among the new parent's children (server-side); omitted = append. */
    move: (requirementId: string, parentId: string | null, index?: number) => Promise<Requirement>;
    reorder: (requirementId: string, direction: 'up' | 'down') => Promise<Requirement>;
    remove: (requirementId: string) => Promise<unknown>;
  };
  /** Remove one link (`DELETE /projects/{id}/links`) of the open project. */
  unlink: (configId: string, sourceId: string, targetId: string) => Promise<unknown>;
  /** Move a module one place up/down in its project's module order (`module.reorder`). */
  reorderModule: (projectId: string, moduleId: string, direction: 'up' | 'down') => Promise<Module>;
  /**
   * Open the module holding `requirementId` (any module of the open project)
   * and select, expand to and scroll to it. Rejects when it cannot be found.
   */
  goToRequirement: (requirementId: string) => Promise<void>;
  /** Select a requirement of the open module and reveal it in the sidebar's Module tab. */
  showInTree: (requirementId: string) => void;
  /** Open a project / module and switch the sidebar to its tab. */
  openProject: (projectId: string) => void;
  openModule: (projectId: string, moduleId: string) => void;
  /** Start loading the templates list (it is fetched lazily, when a template menu or picker opens). */
  requestTemplates: () => void;
}

/** Application state every action can read. Values are null/empty when the route does not provide them. */
export interface AppState {
  projectId: string | null;
  moduleId: string | null;
  project: Project | null;
  /** Set on a module route once the module has loaded. */
  module: Module | null;
  /** The open project's modules in display order (empty outside a project route or while loading). */
  modules: Module[];
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
  /** Loaded lazily: `isLoading` until `commands.requestTemplates()` was called and the list arrived. */
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
  /**
   * Whether the clicked element supports the action at all. Menus hide
   * unsupported actions (and the dividers they leave dangling); shortcuts
   * and `run` ignore them. Omitted: always supported.
   */
  isSupported?(ctx: ActionContext<E>): boolean;
  /** Disabled (menus) / not triggered (shortcuts) when false. */
  isAvailable(ctx: ActionContext<E>): boolean;
  /** Toggle actions: shows a check mark. */
  isChecked?(ctx: ActionContext<E>): boolean;
  /** Ends with an ellipsis in menus when the action opens a dialog. */
  opensDialog?: boolean;
  run(ctx: ActionContext<E>): void;
  /** Present for actions that are a list of choices: rendered as a submenu instead of an item. */
  items?(ctx: ActionContext<E>): DynamicMenuItem[];
  /** Called when the submenu of `items` is shown (e.g. to start loading its data lazily). */
  onItemsShown?(ctx: ActionContext<E>): void;
}

/** A menu layout: which actions appear where. The same registry action can appear in several menus. */
export type MenuEntry =
  | { kind: 'action'; id: string }
  | { kind: 'divider' }
  | { kind: 'submenu'; label: string; icon?: ActionIcon; entries: MenuEntry[] };

/** A context menu to show at a screen position (see `AppActionsValue.openMenu`). */
export interface MenuRequest {
  x: number;
  y: number;
  entries: MenuEntry[];
  /** The clicked element(s), keyed by element type. */
  targets?: ActionTargetOverrides;
  /** Optional heading (e.g. the clicked element's name). */
  label?: string;
}
