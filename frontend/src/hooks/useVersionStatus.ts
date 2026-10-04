import { useBackendVersion } from '../api/queries';
import { FRONTEND_VERSION_INFO, isVersionMismatch } from '../buildInfo';

/**
 * This frontend's version/build, the backend's (`GET /version`), and whether
 * they disagree. Shared by the footer and Help ▸ About.
 */
export function useVersionStatus() {
  const backend = useBackendVersion();
  const frontend = FRONTEND_VERSION_INFO;
  const mismatch = isVersionMismatch(frontend, backend.data);
  const backendLabel = backend.data
    ? `Backend: ReqManTool ${backend.data.version} · build ${backend.data.build}`
    : backend.isError
      ? `Backend version unavailable: ${backend.error.message}`
      : 'Checking backend version…';
  return { frontend, backend, mismatch, backendLabel };
}
