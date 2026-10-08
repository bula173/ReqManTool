export type AttributeType = 'string' | 'text' | 'integer' | 'boolean' | 'enum' | 'date';

/**
 * What a module holds (project format 5). Omitted by the backend for the
 * default `requirements`; read it with `moduleTypeOf` (`api/moduleTypes.ts`).
 */
export type ModuleType = 'requirements' | 'test_cases' | 'test_execution';

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
  attribute_ids: string[];
  /**
   * Shared column widths in pixels, keyed by attribute id (and `'@id'` for
   * the Id column). Omitted when the view stores none. Per-browser widths
   * (localStorage) apply on top of them.
   */
  column_widths?: Record<string, number>;
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
  modules: { key: string; name: string; prefix: string; type: ModuleType }[];
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
