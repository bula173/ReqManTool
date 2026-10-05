export type AttributeType = 'string' | 'text' | 'integer' | 'boolean' | 'enum';

export interface AttributeDefinition {
  id: string;
  name: string;
  type: AttributeType;
  required: boolean;
  default: unknown;
  choices: string[] | null;
  system: boolean;
}

export interface LinkConfiguration {
  id: string;
  name: string;
  source_module_id: string;
  target_module_id: string;
}

export interface View {
  id: string;
  name: string;
  attribute_ids: string[];
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
  attributes: AttributeDefinition[];
  link_configurations: LinkConfiguration[];
  views: View[];
  /**
   * Stored module display order. Omitted when never set; use the order of
   * `GET /projects/{id}/modules`, which applies it tolerantly.
   */
  module_order?: string[];
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
}

export interface Requirement {
  id: string;
  attributes: Record<string, unknown>;
  parent_id: string | null;
  order: number;
}

export interface CsvImportResult {
  imported: number;
  /** New server-allocated ids, in the file's row order. */
  requirement_ids: string[];
  /** System/link-mirror columns that were present in the file but not imported. */
  ignored_columns: string[];
}

export interface ApiErrorBody {
  detail: string;
}

/** GET /version (WS `version.get`): the backend's tool version and build id. */
export interface VersionInfo {
  version: string;
  build: string;
}

export type TemplateSource = 'builtin' | 'user';

/** One entry of GET /templates (WS `template.list`). */
export interface TemplateSummary {
  /** Namespaced: `builtin:<id>` or `user:<id>`. */
  id: string;
  source: TemplateSource;
  name: string;
  description: string;
  modules: { key: string; name: string; prefix: string }[];
  /** Why the template cannot be used, or null if it is valid. */
  error: string | null;
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
  /** The requirement file has changes that are not committed yet. */
  uncommitted: boolean;
}
