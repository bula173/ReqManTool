import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { WS_URL } from '../api/client';

interface ChangedEvent {
  event: 'changed';
  project_id: string;
}

interface RootChangedEvent {
  event: 'root_changed';
}

type LiveSyncEvent = ChangedEvent | RootChangedEvent;

/**
 * Opens a single app-wide WebSocket connection to the backend's live-sync
 * endpoint. Whenever a {"event":"changed","project_id":...} broadcast
 * arrives, invalidates every cached query touching that project so views
 * refetch automatically (e.g. another tab's edit shows up without reload).
 * A {"event":"root_changed"} broadcast (the data directory was switched)
 * clears the entire cache, since the whole project/module/requirement set
 * is now a different dataset.
 */
export function useLiveSync(): void {
  const queryClient = useQueryClient();
  const queryClientRef = useRef(queryClient);
  queryClientRef.current = queryClient;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      socket = new WebSocket(WS_URL);

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as LiveSyncEvent;
          const qc = queryClientRef.current;
          if (data.event === 'changed' && data.project_id) {
            const projectId = data.project_id;
            qc.invalidateQueries({ queryKey: ['projects'] });
            qc.invalidateQueries({
              predicate: (query) => query.queryKey[0] === 'projects' && query.queryKey[1] === projectId,
            });
          } else if (data.event === 'root_changed') {
            qc.clear();
          }
        } catch {
          // ignore malformed messages
        }
      };

      socket.onclose = () => {
        if (!cancelled) {
          reconnectTimer = setTimeout(connect, 2000);
        }
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);
}
