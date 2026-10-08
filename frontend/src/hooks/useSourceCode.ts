import { REPOSITORY_URL } from '../actions/appActions';
import { useAbout } from '../api/queries';

/**
 * Where the source of the running build is (AGPL-3.0 §13), for the footer
 * and Help ▸ About. The backend decides (`GET /about`: its configurable
 * `source_url` plus the commit/tag of its build); until it answers, or if
 * it cannot be reached, the upstream repository is linked.
 */
export function useSourceCode() {
  const about = useAbout();
  const link = about.data?.source_link ?? REPOSITORY_URL;
  const ref = about.data?.source_ref ?? null;
  const tooltip = about.data
    ? ref
      ? `Source code of this build (${ref}) — ${about.data.license}`
      : `Source repository — build ${about.data.build} is not a published revision`
    : 'Source repository';
  return { about, link, ref, tooltip };
}
