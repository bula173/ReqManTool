/** Paths and formatting shared by the commit browser's pages and links (see `App.tsx` for the routes). */

export function formatCommitDate(iso: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/** The commit details page of a commit (see `App.tsx`). */
export function commitPath(projectId: string, commit: string): string {
  return `/projects/${encodeURIComponent(projectId)}/commits/${commit}`;
}

/** The commit browser page at a scope (see `CommitBrowserPage`). */
export function commitBrowserPath(projectId: string, moduleId?: string | null, requirementId?: string | null): string {
  const params = new URLSearchParams();
  if (moduleId) params.set('module', moduleId);
  if (moduleId && requirementId) params.set('requirement', requirementId);
  const query = params.toString();
  return `/projects/${encodeURIComponent(projectId)}/commits${query ? `?${query}` : ''}`;
}
