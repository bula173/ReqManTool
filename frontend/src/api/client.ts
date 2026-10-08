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

export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.DEV ? `http://${DEV_BACKEND}` : '');

export const WS_URL: string =
  import.meta.env.VITE_WS_URL ?? (import.meta.env.DEV ? `ws://${DEV_BACKEND}/ws` : sameOriginWsUrl());

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
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
