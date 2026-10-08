import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { WS_URL, notifyUnauthorized } from '../api/client';
import { clearDatasetCache, handleProjectRemoved, invalidateCrossProject, queryKeys } from '../api/queries';

interface ChangedEvent {
  event: 'changed';
  project_id: string;
  /** The project was removed (its folder deleted): drop it, close its tabs. */
  removed?: boolean;
}

interface RootChangedEvent {
  event: 'root_changed';
}

interface TemplatesChangedEvent {
  event: 'templates_changed';
}

/** A user AI prompt was created, edited, deleted or imported. */
interface PromptsChangedEvent {
  event: 'prompts_changed';
}

/** A GitLab/GitHub connection was added, changed or removed (app-level; never carries a token). */
interface ConnectionsChangedEvent {
  event: 'connections_changed';
}

type LiveSyncEvent =
  | ChangedEvent
  | RootChangedEvent
  | TemplatesChangedEvent
  | PromptsChangedEvent
  | ConnectionsChangedEvent;

/**
 * Opens a single app-wide WebSocket connection to the backend's live-sync
 * endpoint. Whenever a {"event":"changed","project_id":...} broadcast
 * arrives, invalidates every cached query touching that project so views
 * refetch automatically (e.g. another tab's edit shows up without reload).
 * With `"removed": true` the project was removed: its queries are dropped
 * and `subscribeProjectRemoved` listeners close its tabs (`handleProjectRemoved`).
 * A {"event":"root_changed"} broadcast (the data directory was switched)
 * clears the cache, since the whole project/module/requirement set is now a
 * different dataset; only the backend version query is kept (see
 * clearDatasetCache). A {"event":"templates_changed"} broadcast (a template
 * or module template was saved) refetches both template lists, and
 * {"event":"prompts_changed"} the AI prompt library, {"event":"connections_changed"}
 * the GitLab/GitHub connections (and which connection each project's remote uses).
 *
 * On a reconnect (usually a backend restart, possibly as a different
 * version) the footer's version query is refetched; on the first connect it
 * is refetched only if it settled in error (the page loaded while the
 * backend was down).
 */
/** Close code of the server edition when the session of the socket ended (logout, expiry). */
const WS_CLOSE_UNAUTHENTICATED = 4401;

export function useLiveSync(): void {
  const queryClient = useQueryClient();
  const queryClientRef = useRef(queryClient);
  queryClientRef.current = queryClient;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    let hasConnected = false;

    const connect = () => {
      if (cancelled) return;
      socket = new WebSocket(WS_URL);

      socket.onopen = () => {
        // A reconnect usually means the backend restarted, possibly as a
        // different version: refresh the footer's version check. On the first
        // open, refresh it only if it failed (backend was down at page load).
        const qc = queryClientRef.current;
        if (hasConnected || qc.getQueryState(queryKeys.version)?.status === 'error') {
          qc.invalidateQueries({ queryKey: queryKeys.version });
        }
        hasConnected = true;
      };

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as LiveSyncEvent;
          const qc = queryClientRef.current;
          if (data.event === 'changed' && data.project_id && data.removed) {
            handleProjectRemoved(qc, data.project_id);
          } else if (data.event === 'changed' && data.project_id) {
            const projectId = data.project_id;
            qc.invalidateQueries({ queryKey: ['projects'] });
            qc.invalidateQueries({
              predicate: (query) => query.queryKey[0] === 'projects' && query.queryKey[1] === projectId,
            });
            // References and incoming cross-project links of every project may depend on it.
            invalidateCrossProject(qc);
          } else if (data.event === 'root_changed') {
            clearDatasetCache(qc);
          } else if (data.event === 'templates_changed') {
            qc.invalidateQueries({ queryKey: queryKeys.templates });
            qc.invalidateQueries({ queryKey: queryKeys.moduleTemplates });
          } else if (data.event === 'prompts_changed') {
            qc.invalidateQueries({ queryKey: queryKeys.prompts });
          } else if (data.event === 'connections_changed') {
            qc.invalidateQueries({ queryKey: queryKeys.providerConnections });
            qc.invalidateQueries({
              predicate: (query) => query.queryKey[2] === 'git' && query.queryKey[3] === 'provider',
            });
          }
        } catch {
          // ignore malformed messages
        }
      };

      socket.onclose = (event) => {
        // Server edition: the backend closed the socket because the session ended.
        if (event.code === WS_CLOSE_UNAUTHENTICATED) notifyUnauthorized();
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
