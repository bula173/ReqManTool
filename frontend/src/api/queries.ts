import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient, type UseQueryResult } from '@tanstack/react-query';
import { API_BASE_URL, api } from './client';
import type {
  ApplyModuleSkeletonResult,
  AttributeDefinition,
  Baseline,
  BaselineCompare,
  BaselineList,
  BaselineSnapshot,
  CommitDetails,
  CommitList,
  CommitQuery,
  CommitSnapshot,
  CreateModuleFromTemplateResult,
  CsvImportMode,
  CsvImportPreview,
  CsvImportResult,
  Prompt,
  PromptFields,
  RenderedPrompt,
  ExecutionReport,
  GitLog,
  GitPullResult,
  GitPushResult,
  GitStatus,
  IncomingLink,
  LinkConfiguration,
  LinkOption,
  Module,
  ModuleFolder,
  ModuleSkeletonPreview,
  ModuleTemplateSummary,
  ModuleTreeEntry,
  ModuleType,
  Project,
  ProjectListEntry,
  ProjectLock,
  ProjectReferences,
  ProjectRemovalInfo,
  ProjectRemovalResult,
  ProjectProviderInfo,
  ProviderConnection,
  ProviderKind,
  ProviderNamespace,
  ProviderRepositoryPage,
  PublishProjectResult,
  Requirement,
  ReviewStatus,
  StartReviewResult,
  RequirementHistory,
  RowStyle,
  SetAttributeModulesResult,
  TestResultsImportOptions,
  TestResultsImportResult,
  TestResultsPreview,
  TemplateSummary,
  TraceabilityReport,
  VerificationReport,
  VerificationStatuses,
  VersionInfo,
  AboutInfo,
  ThirdPartyLicenses,
  View,
  ViewCreate,
  ViewUpdate,
} from './types';

export const queryKeys = {
  projects: ['projects'] as const,
  project: (projectId: string) => ['projects', projectId] as const,
  modules: (projectId: string) => ['projects', projectId, 'modules'] as const,
  module: (projectId: string, moduleId: string) => ['projects', projectId, 'modules', moduleId] as const,
  /** The module tree (folders); not under `modules`, where a module id could collide with it. */
  moduleTree: (projectId: string) => ['projects', projectId, 'module-tree'] as const,
  requirements: (projectId: string, moduleId: string) =>
    ['projects', projectId, 'modules', moduleId, 'requirements'] as const,
  /**
   * Under the module's requirements key, so every requirement mutation (which
   * invalidates that prefix) and live sync also refresh the history.
   */
  requirementHistory: (projectId: string, moduleId: string, requirementId: string) =>
    ['projects', projectId, 'modules', moduleId, 'requirements', requirementId, 'history'] as const,
  gitStatus: (projectId: string) => ['projects', projectId, 'git'] as const,
  gitLog: (projectId: string, limit: number) => ['projects', projectId, 'git', 'log', limit] as const,
  /**
   * Commit browser: under the project's git key, so live sync and every
   * mutation that refreshes the git status refetch the lists too.
   */
  commits: (projectId: string, query: CommitQuery) =>
    [
      'projects',
      projectId,
      'git',
      'commits',
      'list',
      query.moduleId ?? '',
      query.requirementId ?? '',
      query.author ?? '',
      query.text ?? '',
      query.since ?? '',
      query.until ?? '',
    ] as const,
  commit: (projectId: string, commit: string) => ['projects', projectId, 'git', 'commits', 'detail', commit] as const,
  commitSnapshot: (projectId: string, commit: string) =>
    ['projects', projectId, 'git', 'commits', 'snapshot', commit] as const,
  commitRequirements: (projectId: string, commit: string, moduleId: string) =>
    ['projects', projectId, 'git', 'commits', 'snapshot', commit, 'modules', moduleId] as const,
  baselines: (projectId: string) => ['projects', projectId, 'baselines'] as const,
  baseline: (projectId: string, name: string) => ['projects', projectId, 'baselines', name] as const,
  baselineRequirements: (projectId: string, name: string, moduleId: string) =>
    ['projects', projectId, 'baselines', name, 'modules', moduleId, 'requirements'] as const,
  baselineCompare: (projectId: string, from: string, to: string) =>
    ['projects', projectId, 'baselines', 'compare', from, to] as const,
  /** Mutation key of "Enable history (git)", for pending indicators (`useIsMutating`). */
  enableHistory: (projectId: string) => ['enableHistory', projectId] as const,
  /** Mutation keys of Push / Pull, for pending indicators (`useIsMutating`). */
  gitPush: (projectId: string) => ['gitPush', projectId] as const,
  gitPull: (projectId: string) => ['gitPull', projectId] as const,
  /**
   * Reports live under the project key, so live sync (`changed` for the
   * project) refetches them; they are read-only and never invalidated by a
   * mutation of this client, but have no stale time (refetched on mount).
   */
  traceability: (projectId: string, configIds: string[]) =>
    ['projects', projectId, 'reports', 'traceability', ...configIds] as const,
  verificationReport: (projectId: string, moduleId: string) =>
    ['projects', projectId, 'reports', 'verification', moduleId] as const,
  executionReport: (projectId: string, moduleId: string) =>
    ['projects', projectId, 'reports', 'execution', moduleId] as const,
  /** The computed Verification status column / detail field of a requirements module. */
  verificationStatuses: (projectId: string, moduleId: string) =>
    ['projects', projectId, 'reports', 'verification-status', moduleId] as const,
  /**
   * Project references and incoming cross-project links depend on other
   * projects too: every successful mutation of this client and every live-sync
   * `changed` event (any project) refetches them (see `invalidateCrossProject`).
   */
  projectReferences: (projectId: string) => ['projects', projectId, 'references'] as const,
  /** What removing the project would do (blockers, recovery); refetched with the project. */
  projectRemoval: (projectId: string) => ['projects', projectId, 'removal'] as const,
  incomingLinks: (projectId: string, moduleId: string) => ['projects', projectId, 'incoming-links', moduleId] as const,
  config: ['config'] as const,
  version: ['version'] as const,
  /** Under `version`: refetched with it on reconnect, and kept across data directory switches. */
  about: ['version', 'about'] as const,
  backendThirdParty: ['version', 'third-party'] as const,
  /** The frontend bundle's own licence list (static file next to the bundle). */
  frontendThirdParty: ['frontend-third-party'] as const,
  templates: ['templates'] as const,
  /** Module templates (GET /module-templates); fetched lazily, refetched on `templates_changed`. */
  moduleTemplates: ['module-templates'] as const,
  /** The AI prompt library (GET /prompts); refetched on `prompts_changed`. */
  prompts: ['prompts'] as const,
  /** GitLab/GitHub connections (app-level); refetched on `connections_changed`. */
  providerConnections: ['provider-connections'] as const,
  providerNamespaces: (connectionId: string) => ['provider-connections', connectionId, 'namespaces'] as const,
  providerRepositories: (connectionId: string, search: string, page: number) =>
    ['provider-connections', connectionId, 'repositories', search, page] as const,
  /** Which connection the project's remote belongs to (local, cheap: under `git`, refreshed with it). */
  gitProvider: (projectId: string) => ['projects', projectId, 'git', 'provider'] as const,
  /**
   * The tracked merge/pull request, live from the provider. Deliberately not
   * under `projects`: every project change would otherwise call the provider
   * API. Refreshed explicitly (review actions, push) and on demand.
   */
  gitReview: (projectId: string) => ['git-review', projectId] as const,
};

// --- version -----------------------------------------------------------

/** The backend's version/build. Refetched by live sync on reconnect, or on first connect after an error. */
export function useBackendVersion(): UseQueryResult<VersionInfo> {
  return useQuery({
    queryKey: queryKeys.version,
    queryFn: () => api.get<VersionInfo>('/version'),
    staleTime: 5 * 60_000,
    retry: 1,
  });
}

/** Licence and corresponding-source location of the backend build (GET /about). */
export function useAbout(): UseQueryResult<AboutInfo> {
  return useQuery({
    queryKey: queryKeys.about,
    queryFn: () => api.get<AboutInfo>('/about'),
    staleTime: 5 * 60_000,
    retry: 1,
  });
}

/** Licences of the backend's Python dependencies (GET /about/third-party); fetched when shown. */
export function useBackendThirdPartyLicenses(enabled: boolean): UseQueryResult<ThirdPartyLicenses> {
  return useQuery({
    queryKey: queryKeys.backendThirdParty,
    queryFn: () => api.get<ThirdPartyLicenses>('/about/third-party'),
    staleTime: Infinity,
    retry: 1,
    enabled,
  });
}

/** Path of the frontend licence list, emitted next to the bundle by vite.config.ts (plugins/thirdPartyLicenses.ts). */
export const FRONTEND_THIRD_PARTY_URL = `${import.meta.env.BASE_URL}assets/third-party-licenses.json`;

/** Licences of the frontend's npm dependencies, generated at build time; fetched from the page origin when shown. */
export function useFrontendThirdPartyLicenses(enabled: boolean): UseQueryResult<ThirdPartyLicenses> {
  return useQuery({
    queryKey: queryKeys.frontendThirdParty,
    queryFn: async () => {
      const response = await fetch(FRONTEND_THIRD_PARTY_URL, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`.trim());
      return (await response.json()) as ThirdPartyLicenses;
    },
    staleTime: Infinity,
    retry: 1,
    enabled,
  });
}

// --- config -----------------------------------------------------------

export function useConfig(): UseQueryResult<{ data_dir: string }> {
  return useQuery({
    queryKey: queryKeys.config,
    queryFn: () => api.get<{ data_dir: string }>('/config'),
  });
}

const datasetClearedListeners = new Set<() => void>();

/**
 * Drops all cached data after the data directory was switched (a different
 * dataset). Same as `qc.clear()` except that the backend version query is
 * kept: the backend process, and so its version, is unchanged, and the
 * footer's observer stays bound to that query instance, so removing it would
 * stop reconnect/focus refetches from reaching the footer until a reload.
 * Then notifies `subscribeDatasetCleared` listeners, so client-side state
 * tied to the old dataset (requirement clipboard, selection) is dropped too.
 */
export function clearDatasetCache(qc: QueryClient): void {
  qc.removeQueries({ predicate: (query) => query.queryKey[0] !== queryKeys.version[0] });
  qc.getMutationCache().clear();
  datasetClearedListeners.forEach((listener) => listener());
}

/**
 * Calls `listener` whenever the data directory was switched (in this tab or,
 * via the `root_changed` live-sync event, by another client); returns the
 * unsubscribe function.
 */
export function subscribeDatasetCleared(listener: () => void): () => void {
  datasetClearedListeners.add(listener);
  return () => {
    datasetClearedListeners.delete(listener);
  };
}

export function useSetDataDir() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (dataDir: string) => api.post<{ data_dir: string }>('/config', { data_dir: dataDir }),
    onSuccess: () => {
      clearDatasetCache(qc);
    },
  });
}

export interface BrowseFoldersResult {
  path: string;
  parent: string | null;
  directories: { name: string; path: string }[];
}

export function useBrowseFolders(path: string | undefined, enabled: boolean): UseQueryResult<BrowseFoldersResult> {
  return useQuery({
    queryKey: ['fs', 'browse', path ?? ''] as const,
    queryFn: () => {
      const query = path ? `?path=${encodeURIComponent(path)}` : '';
      return api.get<BrowseFoldersResult>(`/fs/browse${query}`);
    },
    enabled,
  });
}

// --- projects -----------------------------------------------------------

/** Every project; one that cannot be loaded is a read-only stub carrying `error` (see `isProjectStub`). */
export function useProjects(): UseQueryResult<ProjectListEntry[]> {
  return useQuery({
    queryKey: queryKeys.projects,
    queryFn: () => api.get<ProjectListEntry[]>('/projects'),
  });
}

export function useProject(projectId: string | undefined): UseQueryResult<Project> {
  return useQuery({
    queryKey: queryKeys.project(projectId ?? ''),
    queryFn: () => api.get<Project>(`/projects/${projectId}`),
    enabled: !!projectId,
  });
}

export interface CreateProjectBody {
  name: string;
  description?: string;
  /** `builtin:<id>` or `user:<id>`; omit for a blank project. */
  template_id?: string;
  /** Omit (or null) for a top-level project. */
  parent_project_id?: string | null;
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateProjectBody) => api.post<Project>('/projects', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.projects });
    },
  });
}

/** Moves a project in the subproject tree; `null` makes it top level. */
export function useSetProjectParent(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (parentProjectId: string | null) =>
      api.put<Project>(`/projects/${projectId}/parent`, { parent_project_id: parentProjectId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.projects, exact: true });
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId), exact: true });
    },
  });
}

/**
 * Renames a project's display name (its id and folder stay). 400 for an
 * empty name, 423 while locked, 403 for a non-admin (server edition).
 */
export function useRenameProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, name }: { projectId: string; name: string }) =>
      api.put<Project>(`/projects/${encodeURIComponent(projectId)}/name`, { name }),
    onSuccess: (project) => {
      qc.setQueryData(queryKeys.project(project.id), project);
      qc.invalidateQueries({ queryKey: queryKeys.projects, exact: true });
    },
  });
}

// --- project lifecycle: lock, unlock, remove -------------------------------------

/** Lock a project (optional reason): the server then refuses every change of it until unlocked. */
export function useLockProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, reason }: { projectId: string; reason: string }) =>
      api.put<Project>(`/projects/${encodeURIComponent(projectId)}/lock`, { reason }),
    onSuccess: (project) => {
      qc.setQueryData(queryKeys.project(project.id), project);
      qc.invalidateQueries({ queryKey: queryKeys.projects, exact: true });
      qc.invalidateQueries({ queryKey: queryKeys.projectRemoval(project.id) });
    },
  });
}

/** Unlock a project (the one change a locked project allows). */
export function useUnlockProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) => api.delete<Project>(`/projects/${encodeURIComponent(projectId)}/lock`),
    onSuccess: (project) => {
      qc.setQueryData(queryKeys.project(project.id), project);
      qc.invalidateQueries({ queryKey: queryKeys.projects, exact: true });
      qc.invalidateQueries({ queryKey: queryKeys.projectRemoval(project.id) });
    },
  });
}

/** What removing a project would do: blockers (subprojects, referencing projects, lock) and recovery. */
export function useProjectRemovalInfo(projectId: string | undefined): UseQueryResult<ProjectRemovalInfo> {
  return useQuery({
    queryKey: queryKeys.projectRemoval(projectId ?? ''),
    queryFn: () => api.get<ProjectRemovalInfo>(`/projects/${encodeURIComponent(projectId ?? '')}/removal`),
    enabled: !!projectId,
  });
}

const projectRemovedListeners = new Set<(projectId: string) => void>();

/**
 * Drops a removed project from the cache (its queries are removed, the
 * projects list refetched) and tells `subscribeProjectRemoved` listeners
 * (module tabs, the open route). Called after this client's removal and by
 * live sync for `{"event":"changed","removed":true}`.
 */
export function handleProjectRemoved(qc: QueryClient, projectId: string): void {
  qc.removeQueries({
    predicate: (query) => query.queryKey[0] === 'projects' && query.queryKey[1] === projectId,
  });
  qc.invalidateQueries({ queryKey: queryKeys.projects, exact: true });
  invalidateCrossProject(qc);
  projectRemovedListeners.forEach((listener) => listener(projectId));
}

/** Calls `listener` with the id of every project removed (here or by another client); returns the unsubscribe function. */
export function subscribeProjectRemoved(listener: (projectId: string) => void): () => void {
  projectRemovedListeners.add(listener);
  return () => {
    projectRemovedListeners.delete(listener);
  };
}

/**
 * Removes a project folder. `confirmName`: the project name the user typed;
 * `dataDir`: the data folder the confirmation was shown for (409 when it
 * changed meanwhile). 423 while locked, 409 with subprojects or projects
 * referencing it.
 */
export function useRemoveProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, confirmName, dataDir }: { projectId: string; confirmName: string; dataDir: string | null }) =>
      api.delete<ProjectRemovalResult>(`/projects/${encodeURIComponent(projectId)}`, {
        confirm_name: confirmName,
        data_dir: dataDir,
      }),
    onSuccess: (_result, { projectId }) => handleProjectRemoved(qc, projectId),
  });
}

/** The projects a project references (parent implicitly) and the projects referencing it. */
export function useProjectReferences(projectId: string | undefined): UseQueryResult<ProjectReferences> {
  return useQuery({
    queryKey: queryKeys.projectReferences(projectId ?? ''),
    queryFn: () => api.get<ProjectReferences>(`/projects/${encodeURIComponent(projectId ?? '')}/references`),
    enabled: !!projectId,
  });
}

/**
 * Replaces the projects a project references explicitly (`[]`: none; the
 * parent stays referenced implicitly). 404 for an unknown id, 400 for itself
 * or when a dropped reference is still the target of a link configuration.
 */
export function useSetProjectReferences(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (references: string[]) =>
      api.put<Project>(`/projects/${encodeURIComponent(projectId)}/references`, { references }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.projects, exact: true });
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId), exact: true });
    },
  });
}

/**
 * Refetches the queries that depend on other projects' data: project
 * references (`referenced_by`) and incoming cross-project links. Called after
 * every successful mutation (via `invalidateGitStatus`) and by live sync for a
 * change of any project.
 */
export function invalidateCrossProject(qc: QueryClient): void {
  qc.invalidateQueries({
    predicate: (query) =>
      query.queryKey[0] === 'projects' &&
      (query.queryKey[2] === 'references' || query.queryKey[2] === 'incoming-links'),
  });
}

/** Links into a module from referencing projects (computed on read by the server). */
export function useIncomingLinks(
  projectId: string | undefined,
  moduleId: string | undefined,
  enabled = true,
): UseQueryResult<IncomingLink[]> {
  return useQuery({
    queryKey: queryKeys.incomingLinks(projectId ?? '', moduleId ?? ''),
    queryFn: () =>
      api.get<IncomingLink[]>(
        `/projects/${encodeURIComponent(projectId ?? '')}/incoming-links?module_id=${encodeURIComponent(moduleId ?? '')}`,
      ),
    enabled: enabled && !!projectId && !!moduleId,
  });
}

// --- templates -----------------------------------------------------------

export function useTemplates(enabled = true): UseQueryResult<TemplateSummary[]> {
  return useQuery({
    queryKey: queryKeys.templates,
    queryFn: () => api.get<TemplateSummary[]>('/templates'),
    enabled,
  });
}

export function useSaveProjectAsTemplate(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description?: string }) =>
      api.post<TemplateSummary>(`/projects/${projectId}/save-as-template`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.templates });
    },
  });
}

export function useModuleTemplates(enabled = true): UseQueryResult<ModuleTemplateSummary[]> {
  return useQuery({
    queryKey: queryKeys.moduleTemplates,
    queryFn: () => api.get<ModuleTemplateSummary[]>('/module-templates'),
    enabled,
  });
}

/**
 * Adds a module from a module template (`POST /projects/{id}/modules/from-template`,
 * WS `module.create_from_template`): the module, its heading skeleton, merged
 * attributes and possibly a view, in one change. Everything under the
 * project is refetched (attributes, views, modules, tree).
 */
export function useCreateModuleFromTemplate(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      template_id: string;
      name?: string;
      prefix?: string;
      description?: string;
      /** The module folder to create it in; null/omitted: the top level. */
      folder_id?: string | null;
    }) =>
      api.post<CreateModuleFromTemplateResult>(
        `/projects/${encodeURIComponent(projectId)}/modules/from-template`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    },
  });
}

/**
 * What "Apply chapter template" would add to a module (`POST
 * .../apply-skeleton/preview`, WS `module.preview_skeleton`): read-only, so a
 * plain query keyed by the template; `templateId` null means the built-in
 * template of the module's type.
 */
export function useModuleSkeletonPreview(projectId: string, moduleId: string, templateId: string | null) {
  return useQuery({
    queryKey: [...queryKeys.project(projectId), 'skeleton-preview', moduleId, templateId] as const,
    queryFn: () =>
      api.post<ModuleSkeletonPreview>(
        `/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/apply-skeleton/preview`,
        { template_id: templateId },
      ),
    // Always recomputed when the dialog opens: the module may have changed.
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
}

/**
 * Adds a chapter template's missing headings to an existing module (`POST
 * .../apply-skeleton`, WS `module.apply_skeleton`). Idempotent; existing
 * requirements are never changed. Everything under the project is refetched
 * (requirements, and the Object Type scope if it was extended).
 */
export function useApplyModuleSkeleton(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { template_id: string | null }) =>
      api.post<ApplyModuleSkeletonResult>(
        `/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/apply-skeleton`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    },
  });
}

/** Saves a module's structure (attributes, optional view, headings) as a user module template. */
export function useSaveModuleAsTemplate(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description?: string; view_id?: string | null }) =>
      api.post<ModuleTemplateSummary>(
        `/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/save-as-template`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.moduleTemplates });
    },
  });
}

/**
 * Uploads a template JSON file as a new user template (`POST
 * /templates/import`, WS `template.import`). The file's bytes are sent
 * unchanged with Content-Type application/json; the server checks UTF-8, the
 * template shape and every project rule (400), and rejects a name that any
 * template already uses (409) — nothing is overwritten.
 */
export function useImportTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: Blob) => api.postBody<TemplateSummary>('/templates/import', file, 'application/json'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.templates });
    },
  });
}

export function useAddAttribute(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Omit<AttributeDefinition, 'default' | 'system'> & { default?: unknown }) =>
      api.post<Project>(`/projects/${projectId}/attributes`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    },
  });
}

/**
 * Changes the modules an attribute applies to (`PUT
 * /projects/{id}/attributes/{attr}/modules`, WS `attribute.set_modules`) and
 * module types (`module_types`, replaced too); both empty means every module. Non-blank values in modules left out are
 * refused (400) unless `drop_values` is true; the removed ones are returned in
 * `dropped_values`. Invalidates the whole project, since requirements may change.
 */
export function useSetAttributeModules(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      attrId,
      modules,
      moduleTypes,
      dropValues,
    }: {
      attrId: string;
      modules: string[];
      moduleTypes: ModuleType[];
      dropValues: boolean;
    }) =>
      api.put<SetAttributeModulesResult>(`/projects/${projectId}/attributes/${encodeURIComponent(attrId)}/modules`, {
        modules,
        module_types: moduleTypes,
        drop_values: dropValues,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    },
  });
}

/** Replaces the project's row styling rules (`[]` clears them). */
export function useSetRowStyles(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rowStyles: RowStyle[]) =>
      api.put<Project>(`/projects/${projectId}/row-styles`, { row_styles: rowStyles }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    },
  });
}

// --- history (git) -----------------------------------------------------------

/**
 * Invalidates every project's git status, git log, baseline and report queries.
 * Called after any successful mutation (see the QueryClient's MutationCache
 * in main.tsx): each change may add or amend a commit, and a comparison
 * against `current` includes uncommitted changes. Reports (and the computed
 * Verification status column) are derived from the data, so any change may
 * alter them; only the ones in use refetch. Other clients' changes arrive
 * through live sync, which invalidates the whole project prefix.
 */
export function invalidateGitStatus(qc: QueryClient): void {
  invalidateCrossProject(qc);
  qc.invalidateQueries({
    predicate: (query) =>
      query.queryKey[0] === 'projects' &&
      (query.queryKey[2] === 'git' || query.queryKey[2] === 'baselines' || query.queryKey[2] === 'reports'),
  });
}

export function useGitStatus(projectId: string | undefined): UseQueryResult<GitStatus> {
  return useQuery({
    queryKey: queryKeys.gitStatus(projectId ?? ''),
    queryFn: () => api.get<GitStatus>(`/projects/${projectId}/git`),
    enabled: !!projectId,
  });
}

/** "Enable history (git)": creates the project's repository (or branch `current`) and commits. */
export function useEnableHistory(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.enableHistory(projectId),
    mutationFn: () => api.post<GitStatus>(`/projects/${projectId}/git/enable`),
    onSuccess: (status) => {
      qc.setQueryData(queryKeys.gitStatus(projectId), status);
      qc.invalidateQueries({
        predicate: (query) =>
          query.queryKey[0] === 'projects' && query.queryKey[1] === projectId && query.queryKey[6] === 'history',
      });
    },
  });
}

/**
 * Sets (or removes, with null/empty) the project's git remote URL. The server
 * validates the URL with `git ls-remote` (400 when unreachable) and answers
 * with the new git status.
 */
export function useSetGitRemote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (url: string | null) => api.put<GitStatus>(`/projects/${projectId}/git/remote`, { url }),
    onSuccess: (status) => {
      qc.setQueryData(queryKeys.gitStatus(projectId), status);
    },
  });
}

/** Turns the per-project auto-push opt-in on or off; answers with the new git status. */
export function useSetGitAutoPush(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => api.put<GitStatus>(`/projects/${projectId}/git/auto-push`, { enabled }),
    onSuccess: (status) => {
      qc.setQueryData(queryKeys.gitStatus(projectId), status);
    },
  });
}

/**
 * Pushes `current`, `baseline` and the `baseline/*` tags to the project's
 * remote. 409 when the remote has other commits (pull first), 400 without a
 * configured remote or when git fails (network, access).
 */
export function useGitPush(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.gitPush(projectId),
    mutationFn: () => api.post<GitPushResult>(`/projects/${projectId}/git/push`),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.gitStatus(projectId) });
    },
  });
}

/**
 * Fetches the project's remote and fast-forwards `current` (never a merge
 * commit): the project's data may change. Diverged histories are 409: the
 * status then shows the sync conflict and mutations are blocked until the
 * user reconciles the branches in git.
 */
export function useGitPull(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: queryKeys.gitPull(projectId),
    mutationFn: () => api.post<GitPullResult>(`/projects/${projectId}/git/pull`),
    onSettled: () => {
      // A fast-forward may have changed any of the project's files; a failed
      // pull still fetched (the status now shows the sync conflict).
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    },
  });
}

/**
 * "Add project from remote": clones a repository into the data directory and
 * lists it as a project. 409 when its project id is already taken, 400 for an
 * unreachable URL or an invalid clone.
 */
export function useAddProjectFromRemote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (url: string) => api.post<Project>('/projects/from-remote', { url }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.projects });
    },
  });
}

// --- GitLab / GitHub (providers) ----------------------------------------------

/** The GitLab/GitHub connections (never their tokens). */
export function useProviderConnections(enabled = true): UseQueryResult<ProviderConnection[]> {
  return useQuery({
    queryKey: queryKeys.providerConnections,
    queryFn: () => api.get<ProviderConnection[]>('/providers/connections'),
    enabled,
  });
}

/** Adds a connection: the server verifies the token with the provider and stores it in its OS keychain. */
export function useAddProviderConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { provider: ProviderKind; url: string; name: string; token: string }) =>
      api.post<ProviderConnection>('/providers/connections', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.providerConnections }),
  });
}

/** Renames a connection and/or replaces its token (`token` omitted: kept). */
export function useUpdateProviderConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; name?: string; token?: string }) =>
      api.put<ProviderConnection>(`/providers/connections/${encodeURIComponent(id)}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.providerConnections }),
  });
}

/** Removes a connection and its keychain token; project remotes stay configured. */
export function useRemoveProviderConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.delete<{ id: string; removed: boolean }>(`/providers/connections/${encodeURIComponent(id)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.providerConnections });
      qc.invalidateQueries({ predicate: (query) => query.queryKey[2] === 'git' && query.queryKey[3] === 'provider' });
    },
  });
}

/** Where a repository can be created (the user's namespace first, then groups / organisations). */
export function useProviderNamespaces(connectionId: string | null): UseQueryResult<ProviderNamespace[]> {
  return useQuery({
    queryKey: queryKeys.providerNamespaces(connectionId ?? ''),
    queryFn: () => api.get<ProviderNamespace[]>(`/providers/connections/${encodeURIComponent(connectionId ?? '')}/namespaces`),
    enabled: !!connectionId,
    retry: false,
  });
}

/** One page of the user's repositories on a connection, optionally filtered by name. */
export function useProviderRepositories(
  connectionId: string | null,
  search: string,
  page: number,
): UseQueryResult<ProviderRepositoryPage> {
  return useQuery({
    queryKey: queryKeys.providerRepositories(connectionId ?? '', search, page),
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page) });
      if (search) params.set('search', search);
      return api.get<ProviderRepositoryPage>(
        `/providers/connections/${encodeURIComponent(connectionId ?? '')}/repositories?${params.toString()}`,
      );
    },
    enabled: !!connectionId,
    retry: false,
    placeholderData: (previous) => previous,
  });
}

/** "Add project from GitLab/GitHub": the server looks the repository up and clones it. */
export function useAddProjectFromProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { connection_id: string; repository: string }) =>
      api.post<Project>('/projects/from-provider', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.projects });
    },
  });
}

/**
 * "Publish project": creates the repository on the provider, sets it as the
 * project's remote and pushes. A failure after the repository was created
 * still changed the project's status: it is refetched either way.
 */
export function usePublishProject(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      connection_id: string;
      namespace_id: string | null;
      name: string;
      private: boolean;
      description: string;
    }) => api.post<PublishProjectResult>(`/projects/${projectId}/git/publish`, body),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.gitStatus(projectId) });
    },
  });
}

/** Which connection the project's remote belongs to (null connection: none). */
export function useGitProvider(projectId: string | undefined, enabled = true): UseQueryResult<ProjectProviderInfo> {
  return useQuery({
    queryKey: queryKeys.gitProvider(projectId ?? ''),
    queryFn: () => api.get<ProjectProviderInfo>(`/projects/${projectId}/git/provider`),
    enabled: enabled && !!projectId,
  });
}

/** The tracked merge/pull request, live from the provider (state, approvals). */
export function useGitReview(projectId: string | undefined, enabled = true): UseQueryResult<ReviewStatus> {
  return useQuery({
    queryKey: queryKeys.gitReview(projectId ?? ''),
    queryFn: () => api.get<ReviewStatus>(`/projects/${projectId}/git/review`),
    enabled: enabled && !!projectId,
    retry: false,
    staleTime: 30_000,
  });
}

/** Pushes `current` to a review branch and opens (or reuses) a merge/pull request into `current`. */
export function useStartReview(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { branch: string | null; title: string | null }) =>
      api.post<StartReviewResult>(`/projects/${projectId}/git/review`, body),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.gitStatus(projectId) });
      qc.invalidateQueries({ queryKey: queryKeys.gitReview(projectId) });
    },
  });
}

/** Stops tracking the merge/pull request (nothing changes on the provider). */
export function useForgetReview(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<GitStatus>(`/projects/${projectId}/git/review`),
    onSuccess: (status) => {
      qc.setQueryData(queryKeys.gitStatus(projectId), status);
      qc.removeQueries({ queryKey: queryKeys.gitReview(projectId) });
    },
  });
}

/** Recent commits of the project's `current` branch (first-parent, newest first). */
export function useGitLog(projectId: string | undefined, limit = 20, enabled = true): UseQueryResult<GitLog> {
  return useQuery({
    queryKey: queryKeys.gitLog(projectId ?? '', limit),
    queryFn: () => api.get<GitLog>(`/projects/${projectId}/git/log?limit=${limit}`),
    enabled: enabled && !!projectId,
  });
}

// --- commit browser (git, read-only) -------------------------------------------------

/** Commits per page of the commit browser ("Load more" fetches the next). */
export const COMMIT_PAGE_SIZE = 50;

function commitListUrl(projectId: string, query: CommitQuery, offset: number): string {
  const params = new URLSearchParams({ offset: String(offset), limit: String(COMMIT_PAGE_SIZE) });
  if (query.moduleId) params.set('module_id', query.moduleId);
  if (query.requirementId) params.set('requirement_id', query.requirementId);
  if (query.author) params.set('author', query.author);
  if (query.text) params.set('text', query.text);
  if (query.since) params.set('since', query.since);
  if (query.until) params.set('until', query.until);
  return `/projects/${encodeURIComponent(projectId)}/git/commits?${params.toString()}`;
}

/**
 * The commit browser's list at project, module or requirement scope, with
 * author/text/date filters; pages of `COMMIT_PAGE_SIZE` (`fetchNextPage` =
 * "Load more"). Refetched by live sync (`changed` for the project).
 */
export function useCommits(projectId: string | undefined, query: CommitQuery) {
  return useInfiniteQuery({
    queryKey: queryKeys.commits(projectId ?? '', query),
    queryFn: ({ pageParam }) => api.get<CommitList>(commitListUrl(projectId ?? '', query, pageParam)),
    initialPageParam: 0,
    getNextPageParam: (last: CommitList) => (last.has_more ? last.offset + last.entries.length : undefined),
    enabled: !!projectId,
  });
}

/** A commit's details and requirement-level changes (immutable, except the baseline tags on it). */
export function useCommit(projectId: string | undefined, commit: string | undefined): UseQueryResult<CommitDetails> {
  return useQuery({
    queryKey: queryKeys.commit(projectId ?? '', commit ?? ''),
    queryFn: () =>
      api.get<CommitDetails>(`/projects/${encodeURIComponent(projectId ?? '')}/git/commits/${encodeURIComponent(commit ?? '')}`),
    enabled: !!projectId && !!commit,
    staleTime: Infinity,
  });
}

/** The project and modules as they were at a commit (read-only; never changes). */
export function useCommitSnapshot(projectId: string | undefined, commit: string | undefined): UseQueryResult<CommitSnapshot> {
  return useQuery({
    queryKey: queryKeys.commitSnapshot(projectId ?? '', commit ?? ''),
    queryFn: () =>
      api.get<CommitSnapshot>(
        `/projects/${encodeURIComponent(projectId ?? '')}/git/commits/${encodeURIComponent(commit ?? '')}/snapshot`,
      ),
    enabled: !!projectId && !!commit,
    staleTime: Infinity,
  });
}

export function useCommitRequirements(
  projectId: string | undefined,
  commit: string | undefined,
  moduleId: string | undefined,
): UseQueryResult<Requirement[]> {
  return useQuery({
    queryKey: queryKeys.commitRequirements(projectId ?? '', commit ?? '', moduleId ?? ''),
    queryFn: () =>
      api.get<Requirement[]>(
        `/projects/${encodeURIComponent(projectId ?? '')}/git/commits/${encodeURIComponent(commit ?? '')}/modules/${encodeURIComponent(moduleId ?? '')}/requirements`,
      ),
    enabled: !!projectId && !!commit && !!moduleId,
    staleTime: Infinity,
  });
}

// --- baselines (git) -----------------------------------------------------------

export function useBaselines(projectId: string | undefined): UseQueryResult<BaselineList> {
  return useQuery({
    queryKey: queryKeys.baselines(projectId ?? ''),
    queryFn: () => api.get<BaselineList>(`/projects/${projectId}/baselines`),
    enabled: !!projectId,
  });
}

/**
 * Creates a baseline: commits pending changes, tags `baseline/<name>` and
 * fast-forwards the `baseline` branch. 400 for an invalid name, 409 for a
 * duplicate name or a repository that is not recording.
 */
export function useCreateBaseline(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description?: string; lock?: boolean; lock_reason?: string }) =>
      api.post<Baseline & { locked: ProjectLock | null }>(`/projects/${projectId}/baselines`, body),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: queryKeys.baselines(projectId) });
      if (result.locked) {
        // "Lock after baseline": the project and the list show the lock.
        qc.invalidateQueries({ queryKey: queryKeys.projects, exact: true });
        qc.invalidateQueries({ queryKey: queryKeys.project(projectId), exact: true });
      }
    },
  });
}

/** The project and modules as they were at a baseline (read-only; the content never changes). */
export function useBaseline(projectId: string | undefined, name: string | undefined): UseQueryResult<BaselineSnapshot> {
  return useQuery({
    queryKey: queryKeys.baseline(projectId ?? '', name ?? ''),
    queryFn: () => api.get<BaselineSnapshot>(`/projects/${projectId}/baselines/${encodeURIComponent(name ?? '')}`),
    enabled: !!projectId && !!name,
    staleTime: Infinity,
  });
}

export function useBaselineRequirements(
  projectId: string | undefined,
  name: string | undefined,
  moduleId: string | undefined,
): UseQueryResult<Requirement[]> {
  return useQuery({
    queryKey: queryKeys.baselineRequirements(projectId ?? '', name ?? '', moduleId ?? ''),
    queryFn: () =>
      api.get<Requirement[]>(
        `/projects/${projectId}/baselines/${encodeURIComponent(name ?? '')}/modules/${moduleId}/requirements`,
      ),
    enabled: !!projectId && !!name && !!moduleId,
    staleTime: Infinity,
  });
}

/** Compares two baselines, or a baseline with `current` (the working files including uncommitted changes). */
export function useBaselineCompare(
  projectId: string | undefined,
  from: string | undefined,
  to: string | undefined,
): UseQueryResult<BaselineCompare> {
  return useQuery({
    queryKey: queryKeys.baselineCompare(projectId ?? '', from ?? '', to ?? ''),
    queryFn: () =>
      api.get<BaselineCompare>(
        `/projects/${projectId}/baselines/compare?from=${encodeURIComponent(from ?? '')}&to=${encodeURIComponent(to ?? '')}`,
      ),
    enabled: !!projectId && !!from && !!to,
  });
}

export function useRequirementHistory(
  projectId: string | undefined,
  moduleId: string | undefined,
  requirementId: string | undefined,
  enabled = true,
): UseQueryResult<RequirementHistory> {
  return useQuery({
    queryKey: queryKeys.requirementHistory(projectId ?? '', moduleId ?? '', requirementId ?? ''),
    queryFn: () =>
      api.get<RequirementHistory>(`/projects/${projectId}/modules/${moduleId}/requirements/${requirementId}/history`),
    enabled: enabled && !!projectId && !!moduleId && !!requirementId,
  });
}

// --- link configurations -----------------------------------------------------------

export function useLinkConfigurations(projectId: string | undefined): UseQueryResult<LinkConfiguration[]> {
  return useQuery({
    queryKey: ['projects', projectId ?? '', 'link-configurations'] as const,
    queryFn: () => api.get<LinkConfiguration[]>(`/projects/${projectId}/link-configurations`),
    enabled: !!projectId,
  });
}

export function useCreateLinkConfiguration(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      source_module_id: string;
      target_module_id: string;
      /** A referenced project holding the target module; omit for this project. */
      target_project_id?: string | null;
    }) =>
      api.post<LinkConfiguration>(`/projects/${projectId}/link-configurations`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'link-configurations'] });
    },
  });
}

export function useRemoveLinkConfiguration(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (configId: string) =>
      api.delete<{ id: string; removed: boolean }>(`/projects/${projectId}/link-configurations/${configId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'link-configurations'] });
      qc.invalidateQueries({ predicate: (query) => query.queryKey[0] === 'projects' && query.queryKey[1] === projectId });
    },
  });
}

// --- views -----------------------------------------------------------

export function useViews(projectId: string | undefined): UseQueryResult<View[]> {
  return useQuery({
    queryKey: ['projects', projectId ?? '', 'views'] as const,
    queryFn: () => api.get<View[]>(`/projects/${projectId}/views`),
    enabled: !!projectId,
  });
}

export function useCreateView(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ViewCreate) => api.post<View>(`/projects/${projectId}/views`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'views'] });
    },
  });
}

/** Stores (or, with null, removes) a view's shared column widths in project.json. */
export function useSetViewColumnWidths(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ viewId, columnWidths }: { viewId: string; columnWidths: Record<string, number> | null }) =>
      api.put<View>(`/projects/${projectId}/views/${viewId}/column-widths`, { column_widths: columnWidths }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'views'] });
    },
  });
}

/** Changes a view's name, ordered columns, shared widths and/or single-line columns (only the fields given). */
export function useUpdateView(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ viewId, changes }: { viewId: string; changes: ViewUpdate }) =>
      api.put<View>(`/projects/${projectId}/views/${encodeURIComponent(viewId)}`, changes),
    onSuccess: (view) => {
      // Show the saved view right away (callers drop their local draft of it on success).
      qc.setQueryData<View[]>(['projects', projectId, 'views'], (views) =>
        views?.map((v) => (v.id === view.id ? view : v)),
      );
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'views'] });
    },
  });
}

export function useRemoveView(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (viewId: string) =>
      api.delete<{ id: string; removed: boolean }>(`/projects/${projectId}/views/${viewId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'views'] });
    },
  });
}

// --- cross-module requirement lookup ------------------------------------

export interface FindRequirementResult {
  module_id: string;
  requirement: Requirement;
}

export function useFindRequirement(
  projectId: string | undefined,
  requirementId: string | undefined,
  enabled: boolean,
): UseQueryResult<FindRequirementResult> {
  return useQuery({
    queryKey: ['projects', projectId ?? '', 'requirements', requirementId ?? ''] as const,
    queryFn: () => api.get<FindRequirementResult>(`/projects/${projectId}/requirements/${requirementId}`),
    enabled: enabled && !!projectId && !!requirementId,
    staleTime: 60_000,
  });
}

// --- modules -----------------------------------------------------------

export function useModules(projectId: string | undefined): UseQueryResult<Module[]> {
  return useQuery({
    queryKey: queryKeys.modules(projectId ?? ''),
    queryFn: () => api.get<Module[]>(`/projects/${projectId}/modules`),
    enabled: !!projectId,
  });
}

export function useModule(projectId: string | undefined, moduleId: string | undefined): UseQueryResult<Module> {
  return useQuery({
    queryKey: queryKeys.module(projectId ?? '', moduleId ?? ''),
    queryFn: () => api.get<Module>(`/projects/${projectId}/modules/${moduleId}`),
    enabled: !!projectId && !!moduleId,
  });
}

export function useCreateModule(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      prefix: string;
      description?: string;
      type?: ModuleType;
      /** The module folder to create it in; null/omitted: the top level. */
      folder_id?: string | null;
    }) => api.post<Module>(`/projects/${projectId}/modules`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modules(projectId) });
      invalidateModuleTree(qc, projectId);
    },
  });
}

// --- module folders ----------------------------------------------------

/** The module order and tree of a project (the stored tree lives in project.json). */
function invalidateModuleTree(qc: QueryClient, projectId: string): void {
  qc.invalidateQueries({ queryKey: queryKeys.modules(projectId), exact: true });
  qc.invalidateQueries({ queryKey: queryKeys.moduleTree(projectId) });
  qc.invalidateQueries({ queryKey: queryKeys.project(projectId), exact: true });
}

/** The project's module tree as displayed: every module once, folders (empty ones too) in order. */
export function useModuleTree(projectId: string | undefined): UseQueryResult<ModuleTreeEntry[]> {
  return useQuery({
    queryKey: queryKeys.moduleTree(projectId ?? ''),
    queryFn: () => api.get<ModuleTreeEntry[]>(`/projects/${encodeURIComponent(projectId ?? '')}/module-tree`),
    enabled: !!projectId,
  });
}

const folderPath = (projectId: string, folderId: string) =>
  `/projects/${encodeURIComponent(projectId)}/module-folders/${encodeURIComponent(folderId)}`;

/**
 * Module folder operations of any project (the project id is a mutation
 * variable, like `useReorderAnyModule`): create, rename, delete (empty only),
 * move (a folder or a module into a folder; `folderId` null = the top level)
 * and reorder within the parent folder.
 */
export function useCreateModuleFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, name, parentId }: { projectId: string; name: string; parentId: string | null }) =>
      api.post<ModuleFolder>(`/projects/${encodeURIComponent(projectId)}/module-folders`, {
        name,
        parent_id: parentId,
      }),
    onSuccess: (_folder, { projectId }) => invalidateModuleTree(qc, projectId),
  });
}

export function useRenameModuleFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, folderId, name }: { projectId: string; folderId: string; name: string }) =>
      api.put<ModuleFolder>(`${folderPath(projectId, folderId)}/name`, { name }),
    onSuccess: (_folder, { projectId }) => invalidateModuleTree(qc, projectId),
  });
}

export function useDeleteModuleFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, folderId }: { projectId: string; folderId: string }) =>
      api.delete<{ id: string; deleted: boolean }>(folderPath(projectId, folderId)),
    onSuccess: (_result, { projectId }) => invalidateModuleTree(qc, projectId),
  });
}

export function useMoveModuleFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      folderId,
      parentId,
      index,
    }: {
      projectId: string;
      folderId: string;
      parentId: string | null;
      index?: number;
    }) => api.post<ModuleFolder>(`${folderPath(projectId, folderId)}/move`, { folder_id: parentId, index }),
    onSuccess: (_folder, { projectId }) => invalidateModuleTree(qc, projectId),
  });
}

export function useReorderModuleFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, folderId, direction }: { projectId: string; folderId: string; direction: 'up' | 'down' }) =>
      api.post<ModuleFolder>(`${folderPath(projectId, folderId)}/reorder`, { direction }),
    onSuccess: (_folder, { projectId }) => invalidateModuleTree(qc, projectId),
  });
}

/** Moves a module of any project into a module folder (`folderId` null: the top level). */
export function useMoveModule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      moduleId,
      folderId,
      index,
    }: {
      projectId: string;
      moduleId: string;
      folderId: string | null;
      index?: number;
    }) =>
      api.post<Module>(`/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/move`, {
        folder_id: folderId,
        index,
      }),
    onSuccess: (_module, { projectId }) => invalidateModuleTree(qc, projectId),
  });
}

/** Moves a module one place up or down in the project's module order. */
export function useReorderModule(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, direction }: { id: string; direction: 'up' | 'down' }) =>
      api.post<Module>(`/projects/${projectId}/modules/${id}/reorder`, { direction }),
    onSuccess: () => {
      // Only the list order (and the stored tree in the project) changed.
      invalidateModuleTree(qc, projectId);
    },
  });
}

/**
 * Moves a module of any project one place up or down (sidebar module menus):
 * like `useReorderModule`, but the project id is a mutation variable.
 */
export function useReorderAnyModule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, moduleId, direction }: { projectId: string; moduleId: string; direction: 'up' | 'down' }) =>
      api.post<Module>(
        `/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/reorder`,
        { direction },
      ),
    onSuccess: (_module, { projectId }) => invalidateModuleTree(qc, projectId),
  });
}

/**
 * Purge (hard-delete) every requirement of one module. The ids are mutation
 * variables, not hook arguments, so a confirmation dialog purges the module
 * it was opened for even if the route changed meanwhile.
 */
export function usePurgeModule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, moduleId }: { projectId: string; moduleId: string }) =>
      api.post<{ purged: number }>(
        `/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/purge`,
      ),
    onSuccess: (_result, { projectId, moduleId }) => {
      qc.invalidateQueries({ queryKey: queryKeys.requirements(projectId, moduleId) });
    },
  });
}

/** Purge (hard-delete) every requirement of every module of a project. */
export function usePurgeProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) =>
      api.post<{ purged: number }>(`/projects/${encodeURIComponent(projectId)}/purge`),
    onSuccess: (_result, projectId) => {
      // Every module's requirements (and their histories) live under this prefix.
      qc.invalidateQueries({ queryKey: queryKeys.modules(projectId) });
    },
  });
}

function csvImportPath(projectId: string, moduleId: string, mode: CsvImportMode, preview: boolean): string {
  const base = `/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/import/csv`;
  return `${base}${preview ? '/preview' : ''}${mode === 'update' ? '?mode=update' : ''}`;
}

/**
 * CSV content to import: a file's bytes are sent unchanged (the server
 * decodes UTF-8 with or without BOM and rejects other encodings with a 400),
 * pasted text is sent as UTF-8. Content-Type is always text/csv.
 */
export type CsvSource = Blob | string;

function csvBody(source: CsvSource): Blob {
  return typeof source === 'string' ? new Blob([source], { type: 'text/csv' }) : source;
}

/** Read-only preview of a CSV import (what it would create, update and link); changes nothing. */
export function usePreviewCsvImport(projectId: string, moduleId: string) {
  return useMutation({
    mutationFn: ({ source, mode }: { source: CsvSource; mode: CsvImportMode }) =>
      api.postBody<CsvImportPreview>(csvImportPath(projectId, moduleId, mode, true), csvBody(source), 'text/csv'),
  });
}

/**
 * Imports CSV into one module (all-or-nothing). Updates and link columns may
 * change other requirements of the project (link mirrors), so every module's
 * requirements and the project (id counters) are refreshed.
 */
export function useImportCsv(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ source, mode }: { source: CsvSource; mode: CsvImportMode }) =>
      api.postBody<CsvImportResult>(csvImportPath(projectId, moduleId, mode, false), csvBody(source), 'text/csv'),
    onSuccess: () => {
      invalidateProjectRequirements(qc, projectId);
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId), exact: true });
    },
  });
}

// --- AI prompt library ------------------------------------------------------

export function usePrompts(enabled = true): UseQueryResult<Prompt[]> {
  return useQuery({ queryKey: queryKeys.prompts, queryFn: () => api.get<Prompt[]>('/prompts'), enabled });
}

/** Download URL of a prompt file (export). */
export function promptExportUrl(promptId: string): string {
  return `${API_BASE_URL}/prompts/${encodeURIComponent(promptId)}/export`;
}

/** Fills a prompt for a target module and a selection (read-only; a mutation so it runs on demand). */
export function useRenderPrompt(projectId: string) {
  return useMutation({
    mutationFn: (body: {
      prompt_id: string;
      module_id: string;
      requirement_ids?: string[];
      selection_module_id?: string | null;
      include_children?: boolean;
      free_text?: string;
    }) => api.post<RenderedPrompt>(`/projects/${encodeURIComponent(projectId)}/prompts/render`, body),
  });
}

/** Create (no id) or replace (id) a user prompt. */
export function useSavePrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, fields }: { id: string | null; fields: PromptFields }) =>
      id === null
        ? api.post<Prompt>('/prompts', fields)
        : api.put<Prompt>(`/prompts/${encodeURIComponent(id)}`, fields),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.prompts }),
  });
}

export function useDeletePrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/prompts/${encodeURIComponent(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.prompts }),
  });
}

/** Uploads a prompt file as a new user prompt (409 when its name is taken). */
export function useImportPrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: Blob) => api.postBody<Prompt>('/prompts/import', file, 'application/json'),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.prompts }),
  });
}

function testResultsQuery(options: TestResultsImportOptions): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(options)) {
    if (typeof value === 'string' && value.trim() !== '') query.set(key, value.trim());
  }
  const text = query.toString();
  return text ? `?${text}` : '';
}

function testResultsPath(projectId: string, moduleId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/import/test-results`;
}

/**
 * Read-only preview of a test result import (JUnit XML / Robot output.xml):
 * how each result matches a test case. A mutation (it uploads the file) that
 * changes nothing on the server.
 */
export function usePreviewTestResults(projectId: string, moduleId: string) {
  return useMutation({
    mutationFn: ({ file, options }: { file: Blob; options: TestResultsImportOptions }) =>
      api.postBody<TestResultsPreview>(
        `${testResultsPath(projectId, moduleId)}/preview${testResultsQuery(options)}`,
        file,
        'application/xml',
      ),
  });
}

/**
 * Imports test results as execution records of a test_execution module,
 * linked to the matched test cases (all-or-nothing). Records and the linked
 * test cases (their link attribute) change, so every module's requirements
 * and the project (id counters) are refreshed.
 */
export function useImportTestResults(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, options }: { file: Blob; options: TestResultsImportOptions }) =>
      api.postBody<TestResultsImportResult>(
        `${testResultsPath(projectId, moduleId)}${testResultsQuery(options)}`,
        file,
        'application/xml',
      ),
    onSuccess: () => {
      invalidateProjectRequirements(qc, projectId);
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId), exact: true });
    },
  });
}

// --- requirements -----------------------------------------------------------

export function useRequirements(
  projectId: string | undefined,
  moduleId: string | undefined,
): UseQueryResult<Requirement[]> {
  return useQuery({
    queryKey: queryKeys.requirements(projectId ?? '', moduleId ?? ''),
    queryFn: () => api.get<Requirement[]>(`/projects/${projectId}/modules/${moduleId}/requirements`),
    enabled: !!projectId && !!moduleId,
  });
}

export function useAddRequirement(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { attributes: Record<string, unknown>; parent_id?: string | null }) =>
      api.post<Requirement>(`/projects/${projectId}/modules/${moduleId}/requirements`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.requirements(projectId, moduleId) });
    },
  });
}

export function useMoveRequirement(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    /** `index`: 0-based position among the new parent's children; omitted = append as the last child. */
    mutationFn: ({ id, parentId, index }: { id: string; parentId: string | null; index?: number }) =>
      api.put<Requirement>(`/projects/${projectId}/modules/${moduleId}/requirements/${id}/parent`, {
        parent_id: parentId,
        ...(index !== undefined ? { index } : {}),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.requirements(projectId, moduleId) });
    },
  });
}

export function useReorderRequirement(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, direction }: { id: string; direction: 'up' | 'down' }) =>
      api.post<Requirement>(`/projects/${projectId}/modules/${moduleId}/requirements/${id}/reorder`, {
        direction,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.requirements(projectId, moduleId) });
    },
  });
}

export function useCopyRequirement(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, targetParentId }: { id: string; targetParentId: string | null }) =>
      api.post<Requirement>(`/projects/${projectId}/modules/${moduleId}/requirements/${id}/copy`, {
        target_parent_id: targetParentId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.requirements(projectId, moduleId) });
    },
  });
}

export function useUpdateRequirement(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, attributes }: { id: string; attributes: Record<string, unknown> }) =>
      api.put<Requirement>(`/projects/${projectId}/modules/${moduleId}/requirements/${id}`, { attributes }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.requirements(projectId, moduleId) });
    },
  });
}

export function useRemoveRequirement(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.delete<{ id: string; removed: boolean }>(`/projects/${projectId}/modules/${moduleId}/requirements/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.requirements(projectId, moduleId) });
    },
  });
}

// --- links -----------------------------------------------------------

interface LinkResult {
  source: Requirement;
  /** Null after unlinking a cross-project link whose target no longer exists. */
  target: Requirement | null;
}

function invalidateProjectRequirements(qc: ReturnType<typeof useQueryClient>, projectId: string) {
  qc.invalidateQueries({
    predicate: (query) =>
      query.queryKey[0] === 'projects' &&
      query.queryKey[1] === projectId &&
      query.queryKey[2] === 'modules' &&
      query.queryKey[4] === 'requirements',
  });
}

export function useLinkRequirements(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { config_id: string; source_id: string; target_id: string }) =>
      api.post<LinkResult>(`/projects/${projectId}/links`, body),
    onSuccess: () => {
      invalidateProjectRequirements(qc, projectId);
    },
  });
}

/**
 * The link configurations that can join `fromId` (dragged) and `toId` (drop
 * target), in either direction, each oriented the configured way. Fetched on
 * demand (drag-and-drop linking), not cached as a query.
 */
export function fetchLinkOptions(
  projectId: string,
  fromId: string,
  toId: string,
  fromProjectId?: string,
): Promise<LinkOption[]> {
  const query = new URLSearchParams({ from_id: fromId, to_id: toId });
  if (fromProjectId && fromProjectId !== projectId) query.set('from_project_id', fromProjectId);
  return api.get<LinkOption[]>(`/projects/${encodeURIComponent(projectId)}/links/options?${query}`);
}

/**
 * Creates a link in any project (drag-and-drop linking across projects: the
 * link belongs to the project holding the link configuration, `option.project_id`).
 */
export function useLinkInProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      body,
    }: {
      projectId: string;
      body: { config_id: string; source_id: string; target_id: string };
    }) => api.post<LinkResult>(`/projects/${encodeURIComponent(projectId)}/links`, body),
    onSuccess: (_result, { projectId }) => {
      invalidateProjectRequirements(qc, projectId);
    },
  });
}

export function useUnlinkRequirements(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { config_id: string; source_id: string; target_id: string }) =>
      api.delete<LinkResult>(`/projects/${projectId}/links`, body),
    onSuccess: () => {
      invalidateProjectRequirements(qc, projectId);
    },
  });
}

// --- reports (read-only) -----------------------------------------------------------

function configQuery(configIds: string[]): string {
  return configIds.map((id) => `config=${encodeURIComponent(id)}`).join('&');
}

/** Traceability matrix over a chain of link configurations (none: disabled). */
export function useTraceabilityMatrix(
  projectId: string | undefined,
  configIds: string[],
): UseQueryResult<TraceabilityReport> {
  return useQuery({
    queryKey: queryKeys.traceability(projectId ?? '', configIds),
    queryFn: () =>
      api.get<TraceabilityReport>(
        `/projects/${encodeURIComponent(projectId ?? '')}/reports/traceability?${configQuery(configIds)}`,
      ),
    enabled: !!projectId && configIds.length > 0,
  });
}

export function useVerificationReport(
  projectId: string | undefined,
  moduleId: string | undefined,
): UseQueryResult<VerificationReport> {
  return useQuery({
    queryKey: queryKeys.verificationReport(projectId ?? '', moduleId ?? ''),
    queryFn: () =>
      api.get<VerificationReport>(
        `/projects/${encodeURIComponent(projectId ?? '')}/modules/${encodeURIComponent(moduleId ?? '')}/reports/verification`,
      ),
    enabled: !!projectId && !!moduleId,
  });
}

export function useExecutionReport(
  projectId: string | undefined,
  moduleId: string | undefined,
): UseQueryResult<ExecutionReport> {
  return useQuery({
    queryKey: queryKeys.executionReport(projectId ?? '', moduleId ?? ''),
    queryFn: () =>
      api.get<ExecutionReport>(
        `/projects/${encodeURIComponent(projectId ?? '')}/modules/${encodeURIComponent(moduleId ?? '')}/reports/execution`,
      ),
    enabled: !!projectId && !!moduleId,
  });
}

/** Download URL of a traceability matrix export (`csv` | `xlsx`). */
export function traceabilityExportUrl(projectId: string, configIds: string[], format: 'csv' | 'xlsx'): string {
  return `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/reports/traceability/export/${format}?${configQuery(configIds)}`;
}

/** Download URL of a verification / test execution report export (`docx` | `xlsx`). */
export function moduleReportExportUrl(
  projectId: string,
  moduleId: string,
  report: 'verification' | 'execution',
  format: 'docx' | 'xlsx',
): string {
  return `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/reports/${report}/export/${format}`;
}

/**
 * The computed Verification status of each requirement of a requirements
 * module (read-only; never stored). Disabled for other module types.
 */
export function useVerificationStatuses(
  projectId: string | undefined,
  moduleId: string | undefined,
  enabled: boolean,
): UseQueryResult<VerificationStatuses> {
  return useQuery({
    queryKey: queryKeys.verificationStatuses(projectId ?? '', moduleId ?? ''),
    queryFn: () =>
      api.get<VerificationStatuses>(
        `/projects/${encodeURIComponent(projectId ?? '')}/modules/${encodeURIComponent(moduleId ?? '')}/verification-status`,
      ),
    enabled: enabled && !!projectId && !!moduleId,
  });
}
