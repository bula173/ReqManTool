import { useMutation, useQuery, useQueryClient, type QueryClient, type UseQueryResult } from '@tanstack/react-query';
import { invalidatePermissions } from './auth';
import { api } from './client';
import type { DiscoveryList, DiscoveryOpenResult } from './types';

/**
 * Project discovery (server edition only): the ReqManTool projects in the
 * repositories of the user's GitHub/GitLab login, found by a background scan
 * on the server (cached per user). `enabled: false` in the answer when the
 * user logged in otherwise or discovery is off: the section is hidden then.
 *
 * Refetched on every `changed` live-sync event (a repository may have been
 * opened by someone else) and every few seconds while a scan runs.
 */
export const discoveryKeys = {
  list: ['discovery', 'projects'] as const,
};

export function invalidateDiscovery(qc: QueryClient): void {
  qc.invalidateQueries({ queryKey: discoveryKeys.list });
}

/** GET /discovery/projects. `enabled` only in the server edition with a logged-in user. */
export function useDiscoveredProjects(enabled: boolean): UseQueryResult<DiscoveryList> {
  return useQuery({
    queryKey: discoveryKeys.list,
    queryFn: () => api.get<DiscoveryList>('/discovery/projects'),
    enabled,
    staleTime: 60_000,
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && data.enabled && data.status === 'scanning' ? 3_000 : false;
    },
  });
}

/** POST /discovery/refresh: scan the repositories again now. */
export function useRefreshDiscovery() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<DiscoveryList>('/discovery/refresh'),
    onSuccess: (data) => qc.setQueryData(discoveryKeys.list, data),
  });
}

/** POST /discovery/open: clone the repository into the server's data folder (once) and list it as a project. */
export function useOpenDiscoveredProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ provider, repo }: { provider: string; repo: string }) =>
      api.post<DiscoveryOpenResult>('/discovery/open', { provider, repo }),
    onSuccess: () => {
      // The new project, the user's role in it (from the repository permission) and the list's flags.
      invalidatePermissions(qc);
      invalidateDiscovery(qc);
    },
  });
}
