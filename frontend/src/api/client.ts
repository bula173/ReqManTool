import type { ApiErrorBody } from './types';

// Where the API lives. `VITE_API_BASE_URL` / `VITE_WS_URL` always win (an
// empty base URL means "same origin"). Without them, the Vite dev server
// (`npm run dev`, :5173) talks to the dev backend on localhost:8899, and a
// production build talks to the page origin — the backend that serves it
// (`reqman serve`, see backend/reqman/config.py `frontend_dir`).
const DEV_BACKEND = 'localhost:8899';

function sameOriginWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws`;
}

/** The WebSocket URL of the backend at `base` (`''` = same origin). */
function wsUrlFor(base: string): string {
  if (!base) return sameOriginWsUrl();
  return `${base.replace(/^http/, 'ws')}/ws`;
}

// In the desktop app's local mode the Rust side starts `reqman serve` on a
// random port, so the webview cannot know the address at load time:
// `initApiBase()` below fills these in before the app renders. They stay
// `let` (not `const`) because ES module imports are live bindings — read them
// when you build a URL, never copy them into a module-level constant
// (see `documentationLinks()` in actions/appActions.ts).
export let API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.DEV ? `http://${DEV_BACKEND}` : '');

export let WS_URL: string =
  import.meta.env.VITE_WS_URL ?? (import.meta.env.DEV ? `ws://${DEV_BACKEND}/ws` : sameOriginWsUrl());

/**
 * Points `API_BASE_URL` / `WS_URL` at the desktop app's own backend when the
 * UI runs inside Tauri in local mode; a no-op everywhere else (a build with
 * `VITE_API_BASE_URL`, the Vite dev server, a page served by `reqman serve`
 * itself). `DesktopGate` awaits this before the app renders.
 */
export async function setLocalBackend(base: string): Promise<void> {
  API_BASE_URL = base;
  WS_URL = wsUrlFor(base);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

// --- server edition session ----------------------------------------------
// In the local edition none of this is set and requests are sent exactly as
// before. In the server edition (see components/AuthGate.tsx) requests carry
// the session cookie (`credentials: 'include'`, also for the cross-origin dev
// setup), state-changing ones the session's CSRF token, and a 401 sends the
// app back to the login page.

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

const authState: {
  serverMode: boolean;
  csrfToken: string | null;
  onUnauthorized: (() => void) | null;
} = { serverMode: false, csrfToken: null, onUnauthorized: null };

/** Switch the client to the server edition's session handling (send the session cookie). */
export function enableServerSession(): void {
  authState.serverMode = true;
}

/** The current session's CSRF token (from GET /auth/me), or null when logged out. */
export function setCsrfToken(token: string | null): void {
  authState.csrfToken = token;
}

/** Called when the API answers 401 in the server edition (the session ended); null to remove. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  authState.onUnauthorized = handler;
}

/** The session ended (401 from the API, or the live-sync socket was closed for it). */
export function notifyUnauthorized(): void {
  if (authState.serverMode) authState.onUnauthorized?.();
}

/** `fetch` options that carry the server edition's session; empty in the local edition. */
export function sessionInit(method = 'GET'): RequestInit {
  if (!authState.serverMode) return {};
  const headers: Record<string, string> =
    authState.csrfToken && !SAFE_METHODS.has(method.toUpperCase()) ? { 'X-CSRF-Token': authState.csrfToken } : {};
  return { credentials: 'include', headers };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const session = sessionInit(init?.method ?? 'GET');
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    ...(session.credentials ? { credentials: session.credentials } : {}),
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(session.headers as Record<string, string> | undefined),
      ...init?.headers,
    },
  });
  if (res.status === 401) notifyUnauthorized();
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = (await res.json()) as ApiErrorBody;
      if (body?.detail) message = body.detail;
    } catch {
      // ignore parse errors, fall back to statusText
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined }),
  /**
   * POST a raw body (e.g. an uploaded File/Blob) instead of JSON. The bytes are
   * sent as-is, so the server decides how to decode them.
   */
  postBody: <T>(path: string, body: BodyInit, contentType: string) =>
    request<T>(path, { method: 'POST', body, headers: { 'Content-Type': contentType } }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body !== undefined ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'DELETE', body: body !== undefined ? JSON.stringify(body) : undefined }),
};
