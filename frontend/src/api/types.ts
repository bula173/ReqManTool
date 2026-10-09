export type AttributeType = 'string' | 'text' | 'integer' | 'boolean' | 'enum' | 'date';

/**
 * What a module holds (project format 5; `operational_scenarios` since
 * format 9). Omitted by the backend for the default `requirements`; read it
 * with `moduleTypeOf` (`api/moduleTypes.ts`).
 */
export type ModuleType = 'requirements' | 'test_cases' | 'test_execution' | 'operational_scenarios';

export interface AttributeDefinition {
  id: string;
  name: string;
  type: AttributeType;
  required: boolean;
  default: unknown;
  choices: string[] | null;
  system: boolean;
  /**
   * Module ids the attribute applies to, in module order. Omitted (or empty)
   * when it applies to every module. Writing a value in another module is
   * rejected by the server; see `api/applicability.ts`.
   */
  modules?: string[];
  /**
   * Module types the attribute applies to (every module of such a type), in
   * the order `requirements`, `test_cases`, `test_execution`. Omitted when
   * empty. With `modules`, the attribute applies to a module named by either.
   */
  module_types?: ModuleType[];
}

/** A requirement value removed by `PUT /projects/{id}/attributes/{attr}/modules`. */
export interface DroppedAttributeValue {
  module_id: string;
  requirement_id: string;
}

export interface SetAttributeModulesResult {
  project: Project;
  dropped_values: DroppedAttributeValue[];
}

export interface LinkConfiguration {
  id: string;
  name: string;
  source_module_id: string;
  target_module_id: string;
  /**
   * Cross-project configuration: the (referenced) project holding
   * `target_module_id`. Omitted for a module of the configuration's own project.
   */
  target_project_id?: string;
}

/**
 * One way to link two requirements (`GET /projects/{id}/links/options`,
 * WebSocket `requirement.link_options`): a link configuration between their
 * modules, oriented the configured way. `exists`: that link is already there.
 */
export interface LinkOption {
  config: LinkConfiguration;
  /** The project holding the configuration: the link is created there (`POST /projects/{project_id}/links`). */
  project_id: string;
  source_id: string;
  target_id: string;
  source_project_id: string;
  target_project_id: string;
  source_module_id: string;
  target_module_id: string;
  exists: boolean;
}

/**
 * A link into a project from a referencing project (`GET
 * /projects/{id}/incoming-links`, WS `link.incoming`). Cross-project links
 * are stored only in the source project, so the target's requirements carry
 * no mirror attribute for them; these are computed on read.
 */
export interface IncomingLink {
  project_id: string;
  project_name: string;
  config_id: string;
  config_name: string;
  source_module_id: string;
  source_id: string;
  target_module_id: string;
  target_id: string;
}

/** `GET /projects/{id}/references` (WS `project.references`). */
export interface ProjectReferences {
  project_id: string;
  /** The effective references: the parent first (`implicit` unless also listed), then `references`. */
  references: {
    id: string;
    name: string;
    implicit: boolean;
    /** Link configurations of this project that target it. */
    link_configurations: number;
    /** Set when the referenced project is missing or cannot be loaded. */
    error?: string;
  }[];
  /** Projects whose (effective) references include this one, with their configurations targeting it. */
  referenced_by: {
    id: string;
    name: string;
    link_configurations: { id: string; name: string; links: number }[];
  }[];
}

export interface View {
  id: string;
  name: string;
  /** The view's attribute columns in display order (the Id column always comes first). */
  attribute_ids: string[];
  /**
   * Shared column widths in pixels, keyed by attribute id (and `'@id'` for
   * the Id column). Omitted when the view stores none. Unsaved per-browser
   * changes (localStorage) show on top of them until saved or reverted.
   */
  column_widths?: Record<string, number>;
  /** Column keys shown on a single line (no wrap); omitted when every column wraps. */
  single_line?: string[];
  /** Module ids the view is offered on; omitted (or empty): every module. */
  modules?: string[];
}

/**
 * PUT /projects/{id}/views/{view_id} (WS `view.update`): only the fields
 * given change; `column_widths`/`single_line` null clears them.
 */
export interface ViewUpdate {
  name?: string;
  attribute_ids?: string[];
  column_widths?: Record<string, number> | null;
  single_line?: string[] | null;
  modules?: string[] | null;
}

/** POST /projects/{id}/views (WS `view.create`). */
export interface ViewCreate {
  name: string;
  attribute_ids: string[];
  column_widths?: Record<string, number> | null;
  single_line?: string[] | null;
  modules?: string[] | null;
}

/**
 * A row styling rule (project format 3): rows whose enum attribute
 * `attribute_id` has `value` get this font color, background color and bold.
 * Colors are `#rrggbb` (lower-case) or null. The first matching rule wins;
 * see `components/rowStyles.ts`.
 */
export interface RowStyle {
  attribute_id: string;
  value: string;
  font_color: string | null;
  background_color: string | null;
  bold: boolean;
}

/**
 * An entry of a project's module tree (`GET /projects/{id}/module-tree`, WS
 * `module_tree.get`), in display order: a module, or a module folder holding
 * further entries. Folders are purely organisational (module ids,
 * requirement ids and links do not depend on them); folder ids are
 * server-allocated.
 */
export type ModuleTreeEntry = ModuleTreeModuleEntry | ModuleTreeFolderEntry;

export interface ModuleTreeModuleEntry {
  module: string;
}

export interface ModuleTreeFolderEntry {
  folder: string;
  name: string;
  children: ModuleTreeEntry[];
}

/** A module folder as the folder operations return it (`parent_id` null: top level). */
export interface ModuleFolder {
  id: string;
  name: string;
  parent_id: string | null;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  /**
   * The generic project this one specializes (subproject tree). Omitted by
   * the backend for a top-level project. In `GET /projects` a dangling or
   * cyclic stored parent is reported as top level.
   */
  parent_project_id?: string | null;
  /**
   * Other projects this one references explicitly (project format 6); the
   * parent is referenced implicitly. Omitted when empty. Link configurations
   * may target modules of referenced projects. Set with `PUT
   * /projects/{id}/references` (WS `project.set_references`).
   */
  references?: string[];
  /**
   * The project lock (project format 8), omitted when unlocked. While set,
   * the server refuses every change of the project (REST 423, WS -32004)
   * except unlocking (`DELETE /projects/{id}/lock`, WS `project.unlock`).
   */
  locked?: ProjectLock | null;
  /**
   * The project template this project was created from (project format 10),
   * omitted for a blank, cloned or imported project. Informational: it lets
   * "Apply chapter template…" pre-select this template's module as the
   * chapter source.
   */
  template_id?: string | null;
  attributes: AttributeDefinition[];
  link_configurations: LinkConfiguration[];
  views: View[];
  /**
   * The stored module tree (project format 7: modules and module folders).
   * Omitted when never set. Use `GET /projects/{id}/module-tree` (or the order
   * of `GET /projects/{id}/modules`), which apply it tolerantly.
   */
  module_order?: ModuleTreeEntry[];
  /**
   * Row styling rules, in priority order. Omitted when the project has none.
   * Set with `PUT /projects/{id}/row-styles` (WS `project.set_row_styles`).
   */
  row_styles?: RowStyle[];
}

/** Who locked a project, when (ISO 8601 UTC) and why; `baseline`: set by "lock after baseline". */
export interface ProjectLock {
  by: string;
  email: string;
  at: string;
  reason: string;
  baseline?: string;
}

/** HTTP status of a change refused because the project is locked. */
export const PROJECT_LOCKED_STATUS = 423;

/** GET /projects/{id}/removal (WS `project.removal_info`): what removing the project would do. */
export interface ProjectRemovalInfo {
  project_id: string;
  name: string;
  locked: ProjectLock | null;
  /** Block the removal (409): move or remove them first. */
  subprojects: { id: string; name: string }[];
  /** Projects referencing this one: block the removal (409); remove the references first. */
  referenced_by: { id: string; name: string }[];
  can_remove: boolean;
  /** The project's git status (null when history is turned off on the server). */
  history: GitStatus | null;
  /** Whether and how the project could be recovered after the removal (server-written). */
  recovery: string;
}

/** DELETE /projects/{id} (WS `project.remove`). */
export interface ProjectRemovalResult {
  removed: string;
  name: string;
  /** Set when part of the folder could not be deleted (left in the data folder's staging directory). */
  leftover: string | null;
}

/**
 * Which data a report was computed from: always the project's current
 * files; `commit` is the repository HEAD then, `baselines` the baselines at
 * that commit (the data is that baseline when nothing is pending).
 */
export interface ReportDataSource {
  /** The git state (`ready`, `paused`, `no_repo`, …; `off` when history is turned off). */
  history: string | null;
  commit: string | null;
  pending_changes: number;
  baselines: string[];
  latest_baseline: { name: string; commit: string } | null;
  locked: ProjectLock | null;
}

/**
 * An entry of GET /projects (WS `project.list`) for a project whose
 * `project.json` cannot be loaded: written by a newer ReqManTool, not valid
 * JSON, wrong shape, … It is read-only: opening it (GET /projects/{id} and
 * every other call) fails with the same error, and it can never be a parent.
 * Always listed top level (its stored parent cannot be trusted); its readable
 * subprojects stay under it.
 */
export interface ProjectStub {
  id: string;
  /** The name stored in the file if it has a usable one, else the id. */
  name: string;
  parent_project_id?: null;
  /** Why the project cannot be loaded. */
  error: string;
}

/** One entry of GET /projects (WS `project.list`): a project, or a stub carrying `error`. */
export type ProjectListEntry = (Project & { error?: undefined }) | ProjectStub;

export function isProjectStub(entry: ProjectListEntry): entry is ProjectStub {
  return typeof entry.error === 'string';
}

export interface Module {
  id: string;
  name: string;
  prefix: string;
  description: string;
  /** Omitted for a `requirements` module; chosen at creation, never changed. */
  type?: ModuleType;
}

export interface Requirement {
  id: string;
  attributes: Record<string, unknown>;
  parent_id: string | null;
  order: number;
}

/** `create`: every row is a new requirement; `update`: a row whose Id is an existing requirement changes it. */
export type CsvImportMode = 'create' | 'update';

export interface CsvImportResult {
  imported: number;
  /** New server-allocated ids, in the file's row order. */
  requirement_ids: string[];
  /** Update mode only: changed requirements, their ids, and matched-but-unchanged ones. */
  updated?: number;
  updated_ids?: string[];
  unchanged?: number;
  /** Only when the file has `Link: <configuration>` columns: links created. */
  linked?: number;
  /** System/link-mirror columns that were present in the file but not imported. */
  ignored_columns: string[];
}

/** Read-only preview of a CSV import (`POST .../import/csv/preview`). */
export interface CsvImportPreview {
  mode: CsvImportMode;
  /** New requirements; `id` is provisional (allocated again on import). Text values are plain. */
  created: { row: number; id: string; parent_id: string | null; attributes: Record<string, unknown> }[];
  updated: {
    row: number;
    id: string;
    changes: { attribute_id: string; name: string; old: unknown; new: unknown }[];
  }[];
  /** Ids of matched requirements the file does not change. */
  unchanged: string[];
  /** Links to create; `exists`: already there (skipped). `target_id` is project-qualified across projects. */
  links: {
    row: number;
    config_id: string;
    config_name: string;
    source_id: string;
    target_id: string;
    exists: boolean;
  }[];
  ignored_columns: string[];
}

export type PromptSelection = 'none' | 'optional' | 'required';
export type PromptImportMode = 'create' | 'update' | 'none';

/** The editable fields of an AI prompt (the prompt file's content). */
export interface PromptFields {
  name: string;
  description: string;
  selection: PromptSelection;
  free_text: PromptSelection;
  free_text_label: string;
  import_mode: PromptImportMode;
  body: string;
}

/** An AI prompt of the library (`GET /prompts`); `error` is set for an invalid file. */
export interface Prompt extends PromptFields {
  id: string;
  source: 'builtin' | 'user';
  /** Placeholders the body uses. */
  placeholders: string[];
  error: string | null;
}

/** `POST /projects/{id}/prompts/render`: the filled prompt text, to copy into an external AI chat. */
export interface RenderedPrompt {
  prompt: Prompt;
  text: string;
  module_id: string;
  module_name: string;
  import_mode: PromptImportMode;
  selected_ids: string[];
}

export interface ApiErrorBody {
  detail: string;
}

/** GET /version (WS `version.get`): the backend's tool version and build id. */
export interface VersionInfo {
  version: string;
  build: string;
}

/**
 * GET /about (WebSocket `about.get`): the backend's version and build, its
 * licence, and where the source of this build is (AGPL-3.0 §13).
 * `source_ref` is the commit or release tag, or null when the build is not
 * tied to a published revision (dev build, local changes); `source_link`
 * then points at the repository itself.
 */
export interface AboutInfo extends VersionInfo {
  license: string;
  source_url: string;
  source_ref: string | null;
  source_link: string;
}

/** One third-party package and its licence (GET /about/third-party, and the frontend bundle's list). */
export interface ThirdPartyPackage {
  name: string;
  version: string;
  license: string;
  homepage: string | null;
  license_text: string | null;
}

export interface ThirdPartyLicenses {
  packages: ThirdPartyPackage[];
}

export type TemplateSource = 'builtin' | 'user';

/** One entry of GET /templates (WS `template.list`). */
export interface TemplateSummary {
  /** Namespaced: `builtin:<id>` or `user:<id>`. */
  id: string;
  source: TemplateSource;
  name: string;
  description: string;
  modules: { key: string; name: string; prefix: string; type: ModuleType }[];
  /** Why the template cannot be used, or null if it is valid. */
  error: string | null;
}

/** One entry of GET /module-templates (WS `module_template.list`). */
export interface ModuleTemplateSummary {
  /** Namespaced: `builtin:<id>` or `user:<id>` (its own namespace, separate from project templates). */
  id: string;
  source: TemplateSource;
  name: string;
  description: string;
  /** The module it creates by default (name, prefix and description can be overridden); null when invalid. */
  module: { name: string; prefix: string; description: string; type: ModuleType } | null;
  /** Names of the attributes the template makes apply to the module. */
  attributes: string[];
  /** The view it adds (unless the project has a view of that name), or null. */
  view: string | null;
  /** Number of headings in its heading skeleton. */
  headings: number;
  /** Why the template cannot be used, or null if it is valid. */
  error: string | null;
}

/** Result of POST /projects/{id}/modules/from-template (WS `module.create_from_template`). */
export interface CreateModuleFromTemplateResult {
  module: Module;
  /** Attributes the project did not have: added optional, scoped to the new module. */
  added_attributes: string[];
  /** Existing scoped attributes that now also apply to the new module. */
  extended_attributes: string[];
  /** The view added, or null (none in the template, or its name was taken). */
  view_id: string | null;
  /** Number of skeleton requirements (headings and informative texts) created. */
  headings: number;
}

/** A chapter heading "Apply chapter template" adds (or would add) to an existing module. */
export interface SkeletonHeading {
  /** Server-allocated id (provisional in a preview). */
  id: string;
  heading: string;
  parent_id: string | null;
  /** Depth in the module tree (1: a root). */
  level: number;
  /** Whether it goes under a heading the module already has. */
  under_existing: boolean;
}

/** `POST .../apply-skeleton/preview` (WS `module.preview_skeleton`): read-only. */
export interface ModuleSkeletonPreview {
  template_id: string;
  /** The module key when the source is a module of a project template, else null. */
  module_key: string | null;
  headings: SkeletonHeading[];
  /** Every requirement that would be created: headings plus their informative texts. */
  requirements: number;
}

/** `POST .../apply-skeleton` (WS `module.apply_skeleton`). */
export interface ApplyModuleSkeletonResult {
  template_id: string;
  /** The module key when the source is a module of a project template, else null. */
  module_key: string | null;
  /** Ids of every requirement created (headings and informative texts); empty when nothing was missing. */
  created: string[];
  headings: SkeletonHeading[];
}

/**
 * A project's history (git) state (GET /projects/{id}/git, WS `project.git_status`):
 * - `off`: git history turned off for this server (REQMAN_GIT=off)
 * - `git_missing`: no usable git executable; plain files
 * - `no_repo`: no repository yet; "Enable history (git)" creates it
 * - `nested`: the data directory is inside another repository's work tree;
 *   ReqManTool never creates or commits there (move the data directory)
 * - `foreign`: the project folder's `.git` points to another repository (a
 *   separate git dir or a linked worktree); never touched, history unsupported
 * - `ready`: own repository on branch `current`; every change is auto-committed
 * - `paused`: own repository on another branch or a detached HEAD; no commits
 * - `error`: git could not inspect the folder
 */
export type GitState =
  | 'off'
  | 'git_missing'
  | 'no_repo'
  | 'nested'
  | 'foreign'
  | 'ready'
  | 'paused'
  | 'error';

export interface GitCommitInfo {
  commit: string;
  subject: string;
  author_name: string;
  author_email: string;
  /** Committer date (ISO 8601): the time of the latest change squashed into the commit. */
  date: string;
}

/**
 * The project's configured git remote (part of `GitStatus`). The URL and the
 * auto-push opt-in live in the repository's own `.git/config` (per instance,
 * never in the shared data files); credentials stay with the user's git
 * (ssh agent / credential helper) and are never stored by ReqManTool.
 */
export interface GitRemoteStatus {
  url: string | null;
  /** Push automatically after each commit/baseline (per-project opt-in). */
  auto_push: boolean;
  /**
   * Editors (project.write without project.admin) may push this project
   * (per-project opt-in a project admin sets in the Git panel).
   */
  allow_editor_push: boolean;
  /** Commits on `current` the remote does not have (null before the first fetch/push). */
  ahead: number | null;
  /** Fetched remote commits not on `current` (null before the first fetch/push). */
  behind: number | null;
  /**
   * `current` and the remote's have diverged: mutations are blocked (409)
   * until the user reconciles the branches in git, then pulls again.
   */
  sync_conflict: boolean;
  conflict_message: string | null;
  /**
   * The merge/pull request this instance opened and tracks (stored in
   * `.git/config`, never in the shared data files); null when none.
   */
  review: TrackedReview | null;
}

/** The tracked merge/pull request of a project (`GitRemoteStatus.review`). */
export interface TrackedReview {
  branch: string;
  number: number;
  url: string | null;
  connection: string | null;
}

export interface GitStatus {
  state: GitState;
  /** Why history is not recorded (or how to resume it); null when all is well. */
  message: string | null;
  git_version: string | null;
  branch: string | null;
  head: GitCommitInfo | null;
  /** The enclosing repository's top level, for `nested`. */
  enclosing_repo: string | null;
  /** Data files that differ from the last commit. */
  pending_changes: number;
  /** The last auto-commit failure (files are saved; retried on the next change), or null. */
  last_error: string | null;
  /** Whether "Enable history (git)" can be used now. */
  can_enable: boolean;
  squash_window_minutes: number;
  /** The identity commits are made with. */
  author: { name: string; email: string } | null;
  /** The configured remote; null while the project has no readable repository. */
  remote: GitRemoteStatus | null;
}

/** POST /projects/{id}/git/push (WS `project.git_push`). */
export interface GitPushResult {
  url: string;
  /** The refs published: `refs/heads/current`, `refs/heads/baseline`, `refs/tags/baseline/*`. */
  pushed: string[];
}

/** POST /projects/{id}/git/pull (WS `project.git_pull`). */
export interface GitPullResult {
  url: string;
  /** Whether `current` was fast-forwarded (the project's files changed). */
  updated: boolean;
  head: string | null;
  /** Non-fatal remarks (diverged baseline branch or tags, remote not published yet). */
  notes: string[];
}

/**
 * One baseline (GET /projects/{id}/baselines, WS `baseline.list`): an
 * annotated `baseline/<name>` tag made by ReqManTool, or a lightweight /
 * foreign `baseline/*` tag the user made by hand (then `annotated` is false
 * or the `Reqman-*` fields are null).
 */
export interface Baseline {
  name: string;
  commit: string;
  annotated: boolean;
  description: string | null;
  /** The baseline's date (the tag's `Reqman-Baseline-Date` trailer, else the tag/creator date). */
  date: string | null;
  tagger: { name: string; email: string } | null;
  tagger_date: string | null;
  tool_version: string | null;
  format_version: number | null;
}

export interface BaselineList {
  available: boolean;
  reason: string | null;
  /** Newest first. */
  baselines: Baseline[];
}

/** GET /projects/{id}/baselines/{name} (WS `baseline.get`): the project at that baseline, read-only. */
export interface BaselineSnapshot {
  name: string;
  commit: string;
  project: Project;
  modules: Module[];
}

/** One side of a baseline comparison: a baseline name, or `current` (the working files on disk). */
export interface BaselineCompareSide {
  name: string;
  commit: string | null;
}

export interface BaselineModuleDiff {
  module_id: string;
  name: string;
  /** The module itself was added/removed between the two sides; null when present on both. */
  change: 'added' | 'removed' | null;
  added: string[];
  removed: string[];
  modified: { id: string; diffs: HistoryDiff[] }[];
  /**
   * The row style of each listed requirement that has one (added/modified:
   * the "to" side's rules and values; removed: the "from" side's). Omitted
   * when none is styled.
   */
  row_styles?: Record<string, RowStyle>;
}

export interface BaselineLinkDiff {
  config_id: string;
  name: string;
  source_id: string;
  target_id: string;
}

/** GET /projects/{id}/baselines/compare?from=&to= (WS `baseline.compare`). */
export interface BaselineCompare {
  from: BaselineCompareSide;
  to: BaselineCompareSide;
  modules: BaselineModuleDiff[];
  attributes: {
    added: { id: string; name: string }[];
    removed: { id: string; name: string }[];
    changed: { id: string; name: string }[];
  };
  links: { added: BaselineLinkDiff[]; removed: BaselineLinkDiff[] };
  summary: {
    modules_added: number;
    modules_removed: number;
    requirements_added: number;
    requirements_removed: number;
    requirements_modified: number;
    links_added: number;
    links_removed: number;
    attributes_added: number;
    attributes_removed: number;
    attributes_changed: number;
  };
}

/** GET /projects/{id}/git/log (WS `project.git_log`): recent commits of `current`, newest first. */
export interface GitLog {
  available: boolean;
  reason: string | null;
  entries: HistoryCommit[];
  truncated: boolean;
}

/** The requirement-level differences of the baseline compare engine (also a commit's changes). */
export type CompareChanges = Omit<BaselineCompare, 'from' | 'to'>;

/** One commit of the commit browser (parsed from the commit object itself). */
export interface CommitListEntry {
  commit: string;
  /** Parent commit ids; the first is the one the commit is compared with. Empty for the first commit. */
  parents: string[];
  author_name: string;
  author_email: string;
  authored_date: string | null;
  committer_name: string;
  committer_email: string;
  /** Committer date (ISO 8601). */
  date: string | null;
  subject: string;
  /** Names of the `baseline/*` tags on this commit. */
  baselines: string[];
  /** Requirements whose files the commit changed (within the module/requirement scope, if any). */
  changed_requirements: number;
}

/**
 * GET /projects/{id}/git/commits (WS `commit.list`): one page, newest first.
 * `available: false` (with `reason`) when the project has no readable repository.
 */
export interface CommitList {
  available: boolean;
  reason: string | null;
  entries: CommitListEntry[];
  offset: number;
  has_more: boolean;
}

/** The scope and filters of a commit list (all optional; dates are YYYY-MM-DD, inclusive). */
export interface CommitQuery {
  moduleId?: string;
  requirementId?: string;
  author?: string;
  text?: string;
  since?: string;
  until?: string;
}

/**
 * GET /projects/{id}/git/commits/{commit} (WS `commit.get`): a commit and what it
 * changed against its first parent, at requirement level, plus `notes` on
 * project/module file changes (module renames, module tree reorders, views, …).
 */
export interface CommitDetails extends Omit<CommitListEntry, 'changed_requirements'>, CompareChanges {
  /** The full commit message. */
  message: string;
  changed_files: number;
  notes: string[];
}

/** GET /projects/{id}/git/commits/{commit}/snapshot (WS `commit.snapshot`): the project at a commit, read-only. */
export interface CommitSnapshot {
  commit: string;
  subject: string;
  date: string | null;
  author_name: string;
  project: Project;
  modules: Module[];
}

/** One difference of a history entry: an attribute value, the parent, or the sibling order. */
export interface HistoryDiff {
  kind: 'attribute' | 'parent' | 'order';
  /** Set for `attribute`. */
  attribute_id: string | null;
  old: unknown;
  new: unknown;
}

export interface HistoryCommit {
  commit: string;
  author_name: string;
  author_email: string;
  authored_date: string;
  /** Committer date (ISO 8601). */
  date: string;
  subject: string;
}

export interface HistoryEntry extends HistoryCommit {
  change: 'added' | 'modified' | 'removed';
  diffs: HistoryDiff[];
  /** The file could not be parsed at this revision. */
  unreadable: boolean;
}

/**
 * GET /projects/{p}/modules/{m}/requirements/{r}/history (WS `requirement.history`).
 * `available: false` (with `reason`) when the project has no readable repository.
 */
export interface RequirementHistory {
  available: boolean;
  reason: string | null;
  /** Newest first. */
  entries: HistoryEntry[];
  /** Per attribute id: the last commit that changed it. */
  blame: Record<string, HistoryCommit>;
  /** Older history exists beyond the listed entries. */
  truncated: boolean;
  /**
   * Who created the requirement (the newest listed creation; null when the
   * history is truncated before it) and who changed it last: computed from
   * git, never stored in the requirement file.
   */
  created: HistoryCommit | null;
  modified: HistoryCommit | null;
  /** The requirement file has changes that are not committed yet. */
  uncommitted: boolean;
}

// --- reports (read-only; see docs/ARCHITECTURE.md "Reports") ---------------------------

/** A module as the reports describe it (`type` always present here). */
export interface ReportModule {
  id: string;
  name: string;
  type: ModuleType;
}

/** A reportable object (headings, information and definitions are left out). */
export interface ReportItem {
  id: string;
  /** Plain text of ObjectText, whitespace collapsed. */
  text: string;
  /** Nesting depth in its module (0: top level). */
  level: number;
}

/** One link configuration of a traceability chain: rows of `from_module_id` × columns of `to_module_id`. */
export interface TraceabilityStep {
  config: LinkConfiguration;
  from_module_id: string;
  to_module_id: string;
  /** [row id, column id] pairs, in row then column order. */
  links: [string, string][];
  total: number;
  covered: number;
  covered_percent: number;
  /** Rows with no link to the next module. */
  uncovered: string[];
  /** Columns with no link from the previous module. */
  orphans: string[];
}

/** GET /projects/{id}/reports/traceability?config=… (WS `report.traceability`). */
export interface TraceabilityReport {
  project_id: string;
  /** The chain's modules in order, each with its items in tree order and how many objects were left out. */
  modules: (ReportModule & { items: ReportItem[]; excluded: number })[];
  steps: TraceabilityStep[];
  /** Every first-module item: the ids reached in each later module, and whether it reaches the last one. */
  trace: { id: string; reached: string[][]; complete: boolean }[];
  summary: { total: number; covered: number; covered_percent: number; traced: number; traced_percent: number };
  /** Items of a requirements module of the chain that reach no item of the next test_cases module. */
  unverified: { module_id: string; test_module_id: string; total: number; ids: string[] }[];
}

export type ImpactDirection = 'upstream' | 'downstream' | 'both';

/** The starting requirements and each reached one: their SIL / Safety Related value and test cases. */
export interface ImpactEntry {
  id: string;
  module_id: string;
  /** Plain text of ObjectText, whitespace collapsed. */
  text: string;
  /** Nesting depth in its module (0: top level). */
  level: number;
  /** The stored value of the module's SIL attribute, or null when there is none. */
  sil: unknown;
  /** The stored value of the module's Safety Related attribute, or null when there is none. */
  safety_related: unknown;
  /** The reportable test cases linked to this requirement. */
  tests: { id: string; module_id: string; text: string }[];
}

/** A reached requirement: how far from a starting one, along which chain. */
export interface ImpactRow extends ImpactEntry {
  direction: 'upstream' | 'downstream';
  /** Number of links from the starting requirement (always ≥ 1: a walk never reaches a start). */
  distance: number;
  /** The previous step of `path`. */
  via: string;
  /** The chain from a starting requirement to this one, inclusive. */
  path: string[];
}

/** A module the analysis touches, with the attributes playing the two roles in it. */
export interface ImpactModule extends ReportModule {
  sil_attribute: { id: string; name: string } | null;
  safety_related_attribute: { id: string; name: string } | null;
}

/** GET /projects/{id}/reports/impact?requirement=… (WS `report.impact`). Computed, never stored. */
export interface ImpactReport {
  project_id: string;
  direction: ImpactDirection;
  depth: number;
  seeds: ImpactEntry[];
  results: ImpactRow[];
  summary: { seeds: number; reached: number; upstream: number; downstream: number; max_distance: number };
  /** Only the modules with a starting or reached requirement, in display order. */
  modules: ImpactModule[];
  data_source: ReportDataSource;
}

export type ExecutionResult = 'passed' | 'failed' | 'blocked' | 'not_run' | 'not_applicable';
export type VerificationStatus = 'verified' | 'failed' | 'not_run' | 'not_covered';
export type ExecutionTestStatus = 'passed' | 'failed' | 'blocked' | 'not_run' | 'not_planned';

/** Which attribute plays each role in an execution module (null: none found). */
export type ExecutionAttributes = Record<
  'result' | 'execution_date' | 'executed_by' | 'version' | 'evidence',
  { id: string; name: string } | null
>;

/** One object of a test_execution module. */
export interface ExecutionRecord {
  id: string;
  module_id: string;
  text: string;
  result: ExecutionResult;
  /** The stored result value, as written. */
  result_value: unknown;
  execution_date: string | null;
  executed_by: unknown;
  version: unknown;
  evidence: unknown;
  /** Execution report only: the test cases the record executes. */
  test_case_ids?: string[];
}

export interface VerificationTest {
  id: string;
  module_id: string;
  text: string;
  result: ExecutionResult;
  /** `execution`: from the latest execution record; `test_case`: the test case's own result attribute; null: none. */
  source: 'execution' | 'test_case' | null;
  /** Number of execution records linked to the test case. */
  executions: number;
  latest: ExecutionRecord | null;
}

/** GET /projects/{id}/modules/{module}/reports/verification (WS `report.verification`). */
export interface VerificationReport {
  project_id: string;
  module: ReportModule;
  test_modules: ReportModule[];
  execution_modules: (ReportModule & { test_module_id: string; attributes: ExecutionAttributes })[];
  requirements: (ReportItem & { status: VerificationStatus; tests: VerificationTest[] })[];
  totals: Record<VerificationStatus | 'total' | 'excluded', number>;
  percent: Record<VerificationStatus, number>;
  data_source: ReportDataSource;
}

export interface ExecutionTestCase {
  id: string;
  module_id: string;
  text: string;
  status: ExecutionTestStatus;
  /** Ids of this module's records linked to the test case. */
  records: string[];
  latest: ExecutionRecord | null;
}

/** GET /projects/{id}/modules/{module}/reports/execution (WS `report.execution`). */
export interface ExecutionReport {
  project_id: string;
  module: ReportModule;
  attributes: ExecutionAttributes;
  test_modules: ReportModule[];
  records: ExecutionRecord[];
  record_counts: Record<ExecutionResult | 'total' | 'excluded', number>;
  test_cases: ExecutionTestCase[];
  test_counts: Record<ExecutionTestStatus | 'total' | 'planned' | 'executed' | 'executed_percent' | 'pass_percent', number>;
  /** Failed and blocked test cases with the requirements they verify. */
  problems: (ExecutionTestCase & { requirements: { id: string; module_id: string; text: string }[] })[];
  unlinked_records: string[];
  versions: string[];
  executors: string[];
  /** [first, last] execution date of the executed records, or null. */
  date_range: [string, string] | null;
  data_source: ReportDataSource;
}

/** GET /projects/{id}/modules/{module}/verification-status (WS `report.verification_status`): computed, never stored. */
export interface VerificationStatuses {
  project_id: string;
  module_id: string;
  /** Per requirement id; objects the reports leave out (headings…) are absent. */
  statuses: Record<string, VerificationStatus>;
}

// --- test result import (JUnit XML, Robot Framework output.xml) ---------------------------

/** Query options of the test result preview / import (all optional; blanks mean default). */
export interface TestResultsImportOptions {
  /** Link configuration to the test_cases module (default: the first one joining the module). */
  config_id?: string;
  /** `@id` (the test case's requirement id) or a test case attribute id. */
  match_attribute?: string;
  executed_by?: string;
  version?: string;
  /** YYYY-MM-DD; default: the date in the file, else today. */
  execution_date?: string;
  /** Written to the evidence attribute with the test name. */
  file_name?: string;
}

export interface TestResultRow {
  index: number;
  name: string;
  full_name: string;
  /** Result category of the file's status (skipped tests: not_run). */
  status: 'passed' | 'failed' | 'not_run';
  raw_status: string;
  message: string;
  execution_date: string;
  state: 'matched' | 'unmatched' | 'ambiguous';
  /** How a matched result matched: the whole name/tag, or a run of words in it. */
  match: 'exact' | 'partial' | null;
  test_case_id: string | null;
  /** Ambiguous: every test case it matches. */
  candidates: string[];
}

/** POST …/import/test-results/preview (WS `requirement.preview_test_results`): read-only. */
export interface TestResultsPreview {
  project_id: string;
  format: 'junit' | 'robot';
  file_name: string;
  module: ReportModule;
  test_module: ReportModule;
  config: LinkConfiguration;
  link_configurations: { id: string; name: string; test_module: ReportModule }[];
  match_attribute: string;
  match_options: { id: string; name: string }[];
  options: { executed_by: string | null; version: string | null; execution_date: string | null };
  attributes: ExecutionAttributes;
  results: TestResultRow[];
  summary: Record<'total' | 'matched' | 'unmatched' | 'ambiguous' | 'passed' | 'failed' | 'not_run', number>;
  test_cases_not_in_file: { id: string; text: string }[];
  /** Non-empty: the import would be refused (nothing written). */
  errors: string[];
  warnings: string[];
}

/** POST …/import/test-results (WS `requirement.import_test_results`). */
export interface TestResultsImportResult {
  imported: number;
  requirement_ids: string[];
  linked: number;
  skipped: number;
  format: 'junit' | 'robot';
  config_id: string;
  test_module_id: string;
  warnings: string[];
}

// --- GitLab / GitHub (providers) ------------------------------------------------

export type ProviderKind = 'gitlab' | 'github';

/**
 * A GitLab/GitHub connection (GET /providers/connections). App-level, not
 * project data. The token lives in the server's OS keychain and is never
 * sent to the browser: `has_token` only says whether one is stored (null
 * when the keychain could not be read, see `token_error`).
 */
export interface ProviderConnection {
  id: string;
  provider: ProviderKind;
  name: string;
  /** The instance's web URL, e.g. https://gitlab.com or https://ghe.example.com. */
  url: string;
  /** The token owner's user name. */
  username: string;
  created: string;
  provider_label: string;
  has_token: boolean | null;
  token_error: string | null;
}

/** Where a repository can be created: the user's own namespace, or a group / organisation. */
export interface ProviderNamespace {
  id: string;
  path: string;
  name: string;
  kind: 'user' | 'group';
}

export interface ProviderRepository {
  id: string;
  /** "group/subgroup/name" (GitLab) or "owner/name" (GitHub). */
  full_path: string;
  name: string;
  description: string;
  private: boolean;
  visibility: string | null;
  web_url: string | null;
  clone_url: string | null;
  ssh_url: string | null;
  default_branch: string | null;
  updated: string | null;
}

/** GET /providers/connections/{id}/repositories: one page. */
export interface ProviderRepositoryPage {
  repositories: ProviderRepository[];
  page: number;
  has_more: boolean;
  /** E.g. GitHub name search limits. */
  note?: string | null;
}

/** POST /projects/{id}/git/publish (WS `project.git_publish`). */
export interface PublishProjectResult {
  repository: ProviderRepository;
  pushed: string[];
  status: GitStatus;
  /** GitHub: the `reqmantool` topic could not be added (publishing itself succeeded). */
  topic_error?: string;
}

/** GET /projects/{id}/git/provider: the connection the project's remote belongs to. */
export interface ProjectProviderInfo {
  connection: { id: string; name: string; provider: ProviderKind; provider_label: string } | null;
  repository: string | null;
}

export interface ReviewApprovals {
  approved_by: string[];
  changes_requested_by: string[];
  /** GitLab: approvals required / still missing (null on GitHub). */
  required: number | null;
  left: number | null;
  approved: boolean;
}

/** A merge request (GitLab) / pull request (GitHub), as the provider reports it. */
export interface ChangeRequest {
  number: number;
  title: string;
  url: string | null;
  state: 'open' | 'merged' | 'closed' | string;
  draft: boolean;
  source_branch: string | null;
  target_branch: string | null;
  author: string | null;
  merge_status: string | null;
  created: string | null;
  updated: string | null;
  approvals: ReviewApprovals | null;
  /** Why the approvals could not be read (verbatim provider error). */
  approvals_error: string | null;
}

/** GET /projects/{id}/git/review (WS `project.git_review_status`). */
export interface ReviewStatus {
  tracked: TrackedReview | null;
  review: ChangeRequest | null;
  provider: ProviderKind | null;
  provider_label?: string;
  repository?: string;
}

/** POST /projects/{id}/git/review (WS `project.git_review_start`). */
export interface StartReviewResult {
  review: ChangeRequest;
  pushed: { url: string; branch: string; head: string; base: string | null; commits: number };
  status: GitStatus;
}

/** A login provider of the server edition (GET /edition). */
export interface AuthProviderInfo {
  id: string;
  /** `gitlab`, `github` (OAuth 2.0) or `oidc` (generic OpenID Connect). */
  type: string;
  name: string;
  /** Backend path that starts the login (append `?next=<path>`). */
  login_url: string;
}

/**
 * GET /edition (WS `edition.get`), public: `local` (no login) or `server`
 * (every request needs a session; `providers` are the login buttons).
 */
export interface EditionInfo {
  mode: 'local' | 'server';
  providers?: AuthProviderInfo[];
}

/** The logged-in user of the server edition. */
export interface SessionUser {
  id: string;
  name: string;
  email: string;
  username: string;
  server_admin: boolean;
}

/** GET /auth/me (server edition; 401 without a session). */
export interface AuthSession {
  user: SessionUser;
  /** Sent as `X-CSRF-Token` with every state-changing request. */
  csrf_token: string;
  /** Unix time (seconds) when the session ends at the latest. */
  expires_at: number;
  /** The user's roles (also GET /auth/permissions, WS `auth.permissions`). */
  permissions?: UserPermissions;
  /**
   * The login account for publish / push / pull (the pseudo connection
   * "login": the OAuth token of this sign-in, server edition), or null when
   * the login provider does not host git repositories (e.g. plain OIDC).
   */
  login_account?: { id: string; type: string; name: string; url: string } | null;
}

/**
 * A role in a project (server edition), lowest first: read; sign off requirements (change only their
 * status); change data; also administer (baselines, lock, attributes, views, link configurations,
 * approval, remove, members, git settings).
 */
export type ProjectRole = 'viewer' | 'reviewer' | 'editor' | 'project-admin';

export const PROJECT_ROLES: ProjectRole[] = ['viewer', 'reviewer', 'editor', 'project-admin'];

export const PROJECT_ROLE_LABELS: Record<ProjectRole, string> = {
  viewer: 'Viewer',
  reviewer: 'Reviewer',
  editor: 'Editor',
  'project-admin': 'Project admin',
};

/** A project's repository on a GitLab/GitHub login provider (server edition: roles from repository permissions). */
export interface RepoBindingInfo {
  provider_id: string;
  provider_type: 'gitlab' | 'github';
  provider_name: string;
  /** e.g. `rbc/rbc-gp`. */
  path: string;
  /** e.g. `gitlab.example.com/rbc/rbc-gp`. */
  repository: string;
}

/** A role from a repository permission (`UserPermissions.repo_roles`). */
export interface RepoRoleInfo extends RepoBindingInfo {
  source: 'repo';
  /** The provider's name for the permission (`Developer`, `Write`, ...); null: no access. */
  permission: string | null;
  role: ProjectRole | null;
  /** Epoch seconds of the lookup. */
  checked_at: number;
  /** The last lookup failed (the role is the last good one, or null). */
  error: string | null;
}

/** POST /admin/access/sync ("Sync access now"). */
export interface AccessSyncResult {
  users: number;
  projects: number;
  errors: { project_id: string; user_id: string; error: string }[];
  /** False when roles from repository permissions are turned off (`server.repo_roles`). */
  enabled: boolean;
}

/** GET /auth/permissions (server edition): the logged-in user's roles. */
export interface UserPermissions {
  /** A server admin may do everything, in every project. */
  server_admin: boolean;
  can_create_projects: boolean;
  /**
   * Project id → role (memberships and group rules naming the project, and repository permissions of
   * projects bound to the login provider; null there: no access, `default_role` does not apply).
   */
  roles: Record<string, ProjectRole | null>;
  /** The role group rules give in every project (`"projects": ["*"]`), or null. */
  default_role: ProjectRole | null;
  /** Projects whose role comes from a repository permission: where from (absent in older servers). */
  repo_roles?: Record<string, RepoRoleInfo>;
}

/** One entry of the server edition's audit log (GET /admin/audit; keys without a value are absent). */
export interface AuditEntry {
  /** ISO 8601, UTC. */
  time: string;
  /** e.g. `auth.login`, `admin.member_set`, `project.lock`, `module.lock_break`. */
  action: string;
  user_id?: string;
  user_name?: string;
  project_id?: string;
  target_user_id?: string;
  target_user_name?: string;
  ip?: string;
  details?: Record<string, unknown>;
}

/** A page of the audit log, newest first; `next_before` fetches the next (older) page, null at the end. */
export interface AuditPage {
  entries: AuditEntry[];
  next_before: number | null;
}

/** A user on the server admins' page (GET /admin/users). */
export interface AdminUser extends SessionUser {
  provider: string;
  active: boolean;
  created_at: string;
  last_login_at: string;
  /** Identity provider groups at the last login. */
  groups: string[];
}

/** One entry of GET /admin/projects/{id}/members. */
export interface ProjectMember {
  user_id: string;
  name: string;
  email: string;
  username: string;
  active: boolean;
  server_admin: boolean;
  role: ProjectRole;
  /**
   * `member`: a membership (editable); `group`: only from a group rule (configured on the server);
   * `repo`: from the user's permission on the project's repository (`permission`, `repository`).
   */
  source: 'member' | 'group' | 'repo';
  /** `repo` entries: the provider's permission name and the repository. */
  permission?: string | null;
  repository?: string;
  /** The user's role comes from the repository instead: this membership / group rule does not apply. */
  superseded?: boolean;
  /** The role a group rule gives (the effective role is the higher one), or null. */
  group_role: ProjectRole | null;
  added_at: string;
}

/** GET/PUT/DELETE /admin/projects/{id}/members (WS `admin.members.*`). */
export interface ProjectMembers {
  project_id: string;
  roles: ProjectRole[];
  members: ProjectMember[];
  /** The project's repository on GitLab/GitHub login providers (roles of their users come from it). */
  repositories?: RepoBindingInfo[];
}

/** A module checked out for editing (server edition; GET /projects/{id}/module-locks). */
export interface ModuleLock {
  project_id: string;
  module_id: string;
  user_id: string;
  user_name: string;
  user_email: string;
  /** ISO 8601 (UTC): when it was checked out, the owner's last change, when it expires without activity. */
  since: string;
  last_activity: string;
  expires_at: string;
  reason: string;
}

/** GET /projects/{id}/module-locks (WS `module.locks`): the project's live module locks. */
export interface ModuleLocks {
  project_id: string;
  /** The server requires a checkout before a module can be changed (`server.require_module_checkout`). */
  require_checkout: boolean;
  /** A lock without activity for this long expires. */
  idle_minutes: number;
  locks: ModuleLock[];
}

/** PUT/DELETE /projects/{id}/modules/{module}/lock, POST .../lock/break. */
export interface ModuleLockResult {
  project_id: string;
  module_id: string;
  lock: ModuleLock | null;
  released?: boolean;
  previous_owner?: string | null;
}

// --- project discovery (server edition) --------------------------------------------

/** A repository of the logged-in user's GitHub/GitLab login that holds a ReqManTool project. */
export interface DiscoveredRepository {
  provider_id: string;
  provider_type: 'github' | 'gitlab';
  provider_name: string;
  /** `owner/name` (GitLab: `group/subgroup/name`). */
  full_name: string;
  web_url: string | null;
  clone_url: string | null;
  default_branch: string | null;
  private: boolean;
  /** Tagged with the `reqmantool` topic. */
  topic: boolean;
  /** From the repository's project.json (branch `current`), when it could be read. */
  project_id: string | null;
  project_name: string | null;
  /** Why a tagged repository cannot be opened (no or no valid project.json), else null. */
  project_error: string | null;
  /** Already cloned into the server's data folder (as `opened_project_id`). */
  already_opened: boolean;
  opened_project_id: string | null;
}

/** GET /discovery/projects, POST /discovery/refresh. */
export type DiscoveryList =
  | { enabled: false; reason: string }
  | {
      enabled: true;
      provider: { id: string; type: 'github' | 'gitlab'; name: string };
      /** `scanning` while a background scan runs (the previous list is shown meanwhile). */
      status: 'idle' | 'scanning';
      /** Unix time (seconds) of the last finished scan, or null. */
      scanned_at: number | null;
      /** The provider's error of the last scan, verbatim. */
      error: string | null;
      warnings: string[];
      /** More repositories than `max_repos` exist (or the scan stopped early): not all were looked at. */
      truncated: boolean;
      max_repos: number;
      repositories: DiscoveredRepository[];
    };

/** POST /discovery/open. */
export interface DiscoveryOpenResult {
  project_id: string;
  /** Cloned now (false: it was already there and was fetched). */
  cloned: boolean;
  updated: boolean;
  notes: string[];
  provider_id: string;
  repository: string;
  permission: string;
  role: ProjectRole;
}
