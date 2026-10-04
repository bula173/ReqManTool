/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_WS_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** Tool version, parsed at build time from `__version__` in backend/reqman/__init__.py (see vite.config.ts). */
declare const __REQMAN_VERSION__: string;
/** Build id injected by vite.config.ts: "<short commit> (<build date>)", or "dev". */
declare const __REQMAN_BUILD__: string;
