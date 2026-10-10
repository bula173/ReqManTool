import { invoke } from '@tauri-apps/api/core';
import { setLocalBackend } from './client';

/**
 * The desktop (Tauri) app's own state, kept in its config file: which mode it
 * runs in and, for `server`, the address it loads instead of the bundled UI.
 * `needsChoice` is true when the user has not decided yet (or asked to decide
 * again from the menu) and the start screen has to be shown.
 */
export type DesktopState = {
  mode: 'local' | 'server' | null;
  serverUrl: string | null;
  baseUrl: string | null;
  needsChoice: boolean;
};

/** True when this page runs in the desktop app's webview. */
export function isDesktop(): boolean {
  return '__TAURI_INTERNALS__' in window;
}

export async function getDesktopState(): Promise<DesktopState> {
  return invoke<DesktopState>('get_app_state');
}

/**
 * Applies the user's choice: `local` starts the bundled backend and points
 * the API at it, `server` stores the address and moves the window there (no
 * local backend is started in that mode).
 */
export async function setDesktopMode(mode: 'local' | 'server', serverUrl?: string): Promise<DesktopState> {
  const state = await invoke<DesktopState>('set_app_mode', { mode, serverUrl: serverUrl ?? null });
  if (state.mode === 'local' && state.baseUrl) await setLocalBackend(state.baseUrl);
  return state;
}

/**
 * Waits for the local backend the Rust side started and points the API at it.
 * It may still be coming up (the first run picks a data folder first), so this
 * retries for a while and falls back to the page origin if it never appears.
 */
export async function connectLocalBackend(): Promise<void> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const state = await getDesktopState();
      if (state.baseUrl) {
        await setLocalBackend(state.baseUrl);
        return;
      }
    } catch {
      // The command is registered before the backend is up; keep trying.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  console.warn('[reqman] the desktop backend did not start; using the page origin');
}
