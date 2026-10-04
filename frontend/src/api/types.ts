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
