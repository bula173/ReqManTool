import type { ComponentType } from 'react';
import type { ProjectAccess } from '../auth/context';
import type {
  AttributeDefinition,
  GitPullResult,
  GitPushResult,
  GitStatus,
  LinkOption,
  Module,
  ModuleLockResult,
  ModuleLocks,
  ModuleTreeEntry,
  ModuleTreeFolderEntry,
  Project,
  ProjectListEntry,
  Requirement,
  TemplateSummary,
  View,
} from '../api/types';
import type { ColumnOrder, ResizableColumns } from '../hooks/columnLayout';
import type { ModuleTabRef } from '../layout/moduleTabs';
import type { PendingLinkSource } from './pendingLink';

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
  /** A module tab of the tab bar: the right-clicked tab, or the active tab by default. */
  tab: ModuleTabRef | null;
  /** A module folder: a sidebar folder node (no default: only its own menus). */
  folder: FolderTarget | null;
  // Project references are project actions (`project.references`).
}

/** Where a module or folder sits in its project's module tree. */
export interface TreePosition {
  /** The folder holding it (null: the top level). */
  parentId: string | null;
  /** Its position among the parent's entries (modules and folders), and their count. */
  index: number;
  siblingCount: number;
}

/** A module with the order of its project's modules (for Move Up/Down availability). */
export interface ModuleTarget {
  projectId: string;
  module: Module;
  /** The project's module ids in display order. */
  moduleIds: string[];
  /** Its place in the module tree (Move Up/Down move it among its folder's entries); null when unknown. */
  position: TreePosition | null;
}

/** A module folder of a project's module tree. */
export interface FolderTarget {
  projectId: string;
  /** The folder as returned by `GET /projects/{id}/module-tree` (its children decide whether it is empty). */
  folder: ModuleTreeFolderEntry;
  position: TreePosition;
}

/** A requirements table column: the Id column (`attribute` null) or an attribute column. */
export interface ColumnTarget {
  columnKey: string;
  label: string;
  attribute: AttributeDefinition | null;
  /** The table's column layout controls (width, wrap). */
  columns: ResizableColumns;
  /** The table's reorderable (attribute) columns as shown; absent or not listing `columnKey`: the column cannot move. */
  order?: ColumnOrder;
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
  /** The requirement at the other end of the link; project-qualified (`rbc-gp:SYRS-0001`) across projects. */
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
  /** GitLab / GitHub: connections (settings), browse-and-clone, publish, review. */
  | { kind: 'providerConnections' }
  | { kind: 'addProjectFromProvider' }
  | { kind: 'publishProject'; projectId: string; projectName: string; projectDescription: string }
  | { kind: 'startReview'; projectId: string }
  | { kind: 'dataDir' }
  | { kind: 'saveAsTemplate'; project: Project }
  | { kind: 'rowStyles'; project: Project }
  | { kind: 'importTemplate' }
  /** `input: 'paste'` opens with the paste-text box (Paste AI result); `mode` preselects update mode. */
  | {
      kind: 'importCsv';
      projectId: string;
      moduleId: string;
      moduleName: string;
      input?: 'file' | 'paste';
      mode?: 'create' | 'update';
    }
  /** The AI prompt library: target module and (optionally) a selected requirement to prefill. */
  | { kind: 'aiPrompts'; projectId: string; moduleId: string; requirementId: string | null }
  /** JUnit XML / Robot output.xml results into a test_execution module. */
  | { kind: 'importTestResults'; projectId: string; moduleId: string; moduleName: string }
  /** `folderId`: the module folder to create it in (null/omitted: the top level). */
  /** `fromTemplate`: open with the module template picker (the list is then fetched right away). */
  | { kind: 'newModule'; projectId: string; folderId?: string | null; fromTemplate?: boolean }
  /** Save a module's attributes, optional view and headings as a user module template. */
  | { kind: 'saveModuleAsTemplate'; projectId: string; moduleId: string; moduleName: string }
  /** A new module folder in `parentId` (null: the top level). */
  | { kind: 'newModuleFolder'; projectId: string; parentId: string | null }
  | { kind: 'renameModuleFolder'; projectId: string; folderId: string; name: string }
  /** Move a module or a module folder into another folder (or the top level). */
  | { kind: 'moveToFolder'; projectId: string; item: { kind: 'module' | 'folder'; id: string; name: string } }
  /** New requirement under `parentId` (null: top level); with `insertIndex`, then placed at that sibling position. */
  | { kind: 'addRequirement'; projectId: string; moduleId: string; parentId: string | null; insertIndex?: number }
  | { kind: 'linkRequirement'; projectId: string; moduleId: string; source: Requirement }
  | { kind: 'moveRequirement'; projectId: string; moduleId: string; source: Requirement }
  | { kind: 'attributeModules'; projectId: string; attribute: AttributeDefinition }
  /**
   * Drag-and-drop or two-step linking with several possible link
   * configurations: the user picks one. `pendingSource`: the two-step link it
   * completes ("Make link"), cleared once the link is created.
   */
  | {
      kind: 'dropLink';
      projectId: string;
      fromId: string;
      toId: string;
      options: LinkOption[];
      pendingSource?: PendingLinkSource;
    }
  /** The projects a project references (its link configurations may target their modules). */
  | { kind: 'projectReferences'; project: Project }
  /** Lock a project (reason); unlocking needs no dialog. */
  | { kind: 'lockProject'; project: Project }
  /** Server edition: check out a module for editing (optional reason); releasing needs no dialog. */
  | { kind: 'checkOutModule'; projectId: string; module: Module }
  /**
   * Remove a project (typed-name confirmation). `dataDir` is the data folder
   * the dialog was opened in: it closes (and the server refuses) when that changed.
   */
  | { kind: 'removeProject'; projectId: string; projectName: string; dataDir: string | null }
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
  /** Expand / collapse the left (navigation) and right (requirement detail) side panels; overlays on narrow screens. */
  setLeftPanelOpen: (open: boolean) => void;
  setDetailPanelOpen: (open: boolean) => void;
  setColorScheme: (scheme: 'light' | 'dark') => void;
  /** Show / hide the computed Verification status column of requirements modules (per browser). */
  setVerificationColumn: (show: boolean) => void;
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
  /** Unlock a project (`DELETE /projects/{id}/lock`); locking and removing go through their dialogs. */
  unlockProject: (projectId: string) => Promise<Project>;
  /** Server edition: give back one's own module lock / break anyone's (project admins); checking out has a dialog. */
  releaseModule: (projectId: string, moduleId: string) => Promise<ModuleLockResult>;
  breakModuleLock: (projectId: string, moduleId: string) => Promise<ModuleLockResult>;
  /**
   * Two-step linking: remember a requirement as the pending link source
   * (shared by every window of this browser and data folder), drop it (with
   * `only`: only while that is still the pending one), and link the pending
   * source to a requirement of the open module (resolved like drag-and-drop
   * linking; the pending source is cleared once linked).
   */
  startLink: (source: PendingLinkSource) => void;
  cancelLink: (only?: PendingLinkSource) => void;
  makeLink: (targetRequirementId: string) => Promise<void>;
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
  /** Move a module one place up/down among the entries of its folder (`module.reorder`). */
  reorderModule: (projectId: string, moduleId: string, direction: 'up' | 'down') => Promise<Module>;
  /**
   * Module tree operations of any project. `folderId`/`parentId` null is the
   * top level; `index` is the 0-based position among the destination's
   * entries (omitted: appended).
   */
  moduleFolders: {
    remove: (projectId: string, folderId: string) => Promise<unknown>;
    reorder: (projectId: string, folderId: string, direction: 'up' | 'down') => Promise<unknown>;
    moveFolder: (projectId: string, folderId: string, parentId: string | null, index?: number) => Promise<unknown>;
    moveModule: (projectId: string, moduleId: string, folderId: string | null, index?: number) => Promise<Module>;
  };
  /**
   * Open the module holding `requirementId` (any module of the open project,
   * or of another project for a project-qualified link end such as
   * `rbc-gp:SYRS-0001`) and select, expand to and scroll to it. Rejects when
   * it cannot be found.
   */
  goToRequirement: (requirementId: string) => Promise<void>;
  /** Select a requirement of the open module and reveal it in the sidebar's Module tab. */
  showInTree: (requirementId: string) => void;
  /** Open a project / module and switch the sidebar to its tab. */
  openProject: (projectId: string) => void;
  openModule: (projectId: string, moduleId: string) => void;
  /** Add a module tab without switching to it (no-op when it is open already). */
  openModuleTab: (projectId: string, moduleId: string) => void;
  /** Close module tabs (by `tabKey`); closing the active one switches to its neighbour or the project page. */
  closeTabs: (keys: string[]) => void;
  /** Move a module tab to a position of the tab bar. */
  moveTab: (key: string, toIndex: number) => void;
  /** Open a module in its own (detached) window, optionally revealing a requirement; false when the popup was blocked. */
  openModuleWindow: (projectId: string, moduleId: string, highlightId?: string) => boolean;
  /** Detach a tab: open its module in its own window and remove the tab; false when the popup was blocked. */
  detachTab: (tab: ModuleTabRef) => boolean;
  /** In a detached window: put the module back in the tab bar of the main window and close this window. */
  reattach: () => void;
  /** Start loading the templates list (it is fetched lazily, when a template menu or picker opens). */
  requestTemplates: () => void;
}

/** Application state every action can read. Values are null/empty when the route does not provide them. */
/** See `Action.requires`. */
export type AccessRequirement = 'projectEditor' | 'projectAdmin' | 'createProjects' | 'connections' | 'serverAdmin';

export interface AppState {
  projectId: string | null;
  moduleId: string | null;
  project: Project | null;
  /** Set on a module route once the module has loaded. */
  module: Module | null;
  /** The open project's modules in display order (empty outside a project route or while loading). */
  modules: Module[];
  /** The open project's module tree (folders; empty outside a project route or while loading). */
  moduleTree: ModuleTreeEntry[];
  /** The open module's requirements (empty outside a module route or while loading). */
  requirements: Requirement[];
  selectedRequirement: Requirement | null;
  clipboard: RequirementClipboard | null;
  /** The pending two-step link source ("Start link"), of this data folder; null when none. */
  pendingLink: PendingLinkSource | null;
  views: View[];
  /** The open project's history (git) status, once loaded. */
  gitStatus: GitStatus | null;
  /**
   * The ids of the locked projects (`Project.locked`): from the projects list
   * and the open project. Actions marked `edits` are unavailable for them.
   */
  lockedProjectIds: ReadonlySet<string>;
  /** What the user may do in which project (server edition roles; everything in the local edition). */
  access: ProjectAccess;
  /**
   * Server edition: the open project's module edit locks (null in the local
   * edition, outside a project or while loading). A module checked out by
   * someone else is read-only (see `isModuleReadOnly`).
   */
  moduleLocks: ModuleLocks | null;
  /** The logged-in user's id (server edition), else null. */
  userId: string | null;
  /** The data folder (null until known); dialogs that delete capture it. */
  dataDir: string | null;
  activeViewId: string | null;
  /**
   * Whether the left (navigation) / right (requirement detail) side panel is
   * open. Remembered per browser; on a narrow screen (`narrowLayout`) both
   * start collapsed and an open one is an overlay (at most one at a time).
   */
  leftPanelOpen: boolean;
  detailPanelOpen: boolean;
  narrowLayout: boolean;
  colorScheme: 'light' | 'dark';
  /** Whether requirements tables show the computed Verification status column (per browser). */
  verificationColumn: boolean;
  /** The open module tabs (this data folder), in tab bar order; empty in a detached window. */
  tabs: ModuleTabRef[];
  /** Whether this window is a detached module window (minimal chrome, no tab bar). */
  detached: boolean;
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
  /**
   * The action changes project data: it is unavailable while the project it
   * applies to is locked (see `isActionAvailable`); the server refuses such
   * a change anyway (423).
   */
  edits?: boolean;
  /**
   * The action changes a module's content (requirements, the links it is the
   * source of, imports): with `edits`, it is also unavailable while another
   * user has that module checked out (server edition; the server answers 423).
   * Default: true for requirement and link actions, false otherwise.
   */
  editsModule?: boolean;
  /**
   * Further permissions the action needs (server edition; always met in the
   * local edition): `projectEditor` / `projectAdmin` a role in the action's
   * project, `createProjects` the right to create projects, `connections`
   * the GitLab/GitHub connections and clone URLs, `serverAdmin` a server admin.
   */
  requires?: AccessRequirement[];
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
