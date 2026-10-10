import { Center, Loader } from '@mantine/core';
import { useEffect, useState, type ReactNode } from 'react';
import { connectLocalBackend, getDesktopState, isDesktop, type DesktopState } from '../api/desktop';
import { StartScreen } from './StartScreen';

/**
 * Desktop-only startup step: before the app mounts, the Tauri side is asked
 * which backend this window should show — the bundled local instance (which
 * is started and pointed at through `API_BASE_URL`) or a server the user
 * picked. Outside the desktop app it renders the children right away.
 */
export function DesktopGate({ children }: { children: ReactNode }) {
  const [choice, setChoice] = useState<DesktopState | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (isDesktop()) {
        try {
          const state = await getDesktopState();
          if (state.mode === 'local') await connectLocalBackend();
          if (!cancelled && state.needsChoice) setChoice(state);
        } catch (error) {
          console.warn('[reqman] desktop startup state unavailable', error);
        }
      }
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!ready) {
    return (
      <Center h="100vh">
        <Loader aria-label="Starting" />
      </Center>
    );
  }
  if (choice) return <StartScreen initial={choice} onChosen={() => setChoice(null)} />;
  return <>{children}</>;
}
