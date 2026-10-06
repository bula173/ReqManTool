import { useMutation, useQuery, useQueryClient, type QueryClient, type UseQueryResult } from '@tanstack/react-query';
import { api } from './client';
import type {
  AttributeDefinition,
  Baseline,
  BaselineCompare,
  BaselineList,
  BaselineSnapshot,
  CsvImportResult,
  GitLog,
  GitPullResult,
  GitPushResult,
  GitStatus,
  LinkConfiguration,
  Module,
  Project,
  ProjectListEntry,
  Requirement,
  RequirementHistory,
  TemplateSummary,
  VersionInfo,
  View,
} from './types';

export const queryKeys = {
  projects: ['projects'] as const,
  project: (projectId: string) => ['projects', projectId] as const,
  modules: (projectId: string) => ['projects', projectId, 'modules'] as const,
  module: (projectId: string, moduleId: string) => ['projects', projectId, 'modules', moduleId] as const,
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
  config: ['config'] as const,
  version: ['version'] as const,
  templates: ['templates'] as const,
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

// --- history (git) -----------------------------------------------------------

/**
 * Invalidates every project's git status, git log and baseline queries.
 * Called after any successful mutation (see the QueryClient's MutationCache
 * in main.tsx): each change may add or amend a commit, and a comparison
 * against `current` includes uncommitted changes. Other clients' changes
 * arrive through live sync, which invalidates the whole project prefix.
 */
export function invalidateGitStatus(qc: QueryClient): void {
  qc.invalidateQueries({
    predicate: (query) =>
      query.queryKey[0] === 'projects' && (query.queryKey[2] === 'git' || query.queryKey[2] === 'baselines'),
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

/** Recent commits of the project's `current` branch (first-parent, newest first). */
export function useGitLog(projectId: string | undefined, limit = 20, enabled = true): UseQueryResult<GitLog> {
  return useQuery({
    queryKey: queryKeys.gitLog(projectId ?? '', limit),
    queryFn: () => api.get<GitLog>(`/projects/${projectId}/git/log?limit=${limit}`),
    enabled: enabled && !!projectId,
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
    mutationFn: (body: { name: string; description?: string }) =>
      api.post<Baseline>(`/projects/${projectId}/baselines`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.baselines(projectId) });
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
    mutationFn: (body: { name: string; source_module_id: string; target_module_id: string }) =>
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
    mutationFn: (body: { name: string; attribute_ids: string[] }) =>
      api.post<View>(`/projects/${projectId}/views`, body),
    onSuccess: () => {
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
    mutationFn: (body: { name: string; prefix: string; description?: string }) =>
      api.post<Module>(`/projects/${projectId}/modules`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modules(projectId) });
    },
  });
}

/** Moves a module one place up or down in the project's module order. */
export function useReorderModule(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, direction }: { id: string; direction: 'up' | 'down' }) =>
      api.post<Module>(`/projects/${projectId}/modules/${id}/reorder`, { direction }),
    onSuccess: () => {
      // Only the list order (and the stored order in the project) changed.
      qc.invalidateQueries({ queryKey: queryKeys.modules(projectId), exact: true });
      qc.invalidateQueries({ queryKey: queryKeys.project(projectId), exact: true });
    },
  });
}

export function usePurgeModule(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ purged: number }>(`/projects/${projectId}/modules/${moduleId}/purge`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.requirements(projectId, moduleId) });
    },
  });
}

export function useImportCsv(projectId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    // Send the file's bytes unchanged: the server decodes UTF-8 (with or
    // without BOM) and rejects other encodings with a 400 instead of importing
    // replacement characters. It also requires Content-Type text/csv.
    mutationFn: (file: Blob) =>
      api.postBody<CsvImportResult>(`/projects/${projectId}/modules/${moduleId}/import/csv`, file, 'text/csv'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.requirements(projectId, moduleId) });
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
  target: Requirement;
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
