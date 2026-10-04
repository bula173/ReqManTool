import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { api } from './client';
import type {
  AttributeDefinition,
  CsvImportResult,
  LinkConfiguration,
  Module,
  Project,
  Requirement,
  View,
} from './types';

export const queryKeys = {
  projects: ['projects'] as const,
  project: (projectId: string) => ['projects', projectId] as const,
  modules: (projectId: string) => ['projects', projectId, 'modules'] as const,
  module: (projectId: string, moduleId: string) => ['projects', projectId, 'modules', moduleId] as const,
  requirements: (projectId: string, moduleId: string) =>
    ['projects', projectId, 'modules', moduleId, 'requirements'] as const,
  config: ['config'] as const,
};

// --- config -----------------------------------------------------------

export function useConfig(): UseQueryResult<{ data_dir: string }> {
  return useQuery({
    queryKey: queryKeys.config,
    queryFn: () => api.get<{ data_dir: string }>('/config'),
  });
}

export function useSetDataDir() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (dataDir: string) => api.post<{ data_dir: string }>('/config', { data_dir: dataDir }),
    onSuccess: () => {
      qc.clear();
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

export function useProjects(): UseQueryResult<Project[]> {
  return useQuery({
    queryKey: queryKeys.projects,
    queryFn: () => api.get<Project[]>('/projects'),
  });
}

export function useProject(projectId: string | undefined): UseQueryResult<Project> {
  return useQuery({
    queryKey: queryKeys.project(projectId ?? ''),
    queryFn: () => api.get<Project>(`/projects/${projectId}`),
    enabled: !!projectId,
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description?: string }) =>
      api.post<Project>('/projects', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.projects });
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
    mutationFn: ({ id, parentId }: { id: string; parentId: string | null }) =>
      api.put<Requirement>(`/projects/${projectId}/modules/${moduleId}/requirements/${id}/parent`, {
        parent_id: parentId,
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
