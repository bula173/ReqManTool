import type { VersionInfo } from './api/types';

/** Version and build id of this frontend bundle, injected by vite.config.ts. */
export const FRONTEND_VERSION_INFO: VersionInfo = {
  version: __REQMAN_VERSION__,
  build: __REQMAN_BUILD__,
};

/** True when the backend reports a different tool version than this bundle was built for. */
export function isVersionMismatch(frontend: VersionInfo, backend: VersionInfo | undefined): boolean {
  return backend !== undefined && backend.version !== frontend.version;
}
