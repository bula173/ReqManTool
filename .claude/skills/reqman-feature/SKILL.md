---
name: reqman-feature
description: "Use when implementing or extending a ReqManTool feature across the Python backend and React frontend, especially REST endpoints, WebSocket JSON-RPC, JSON persistence, requirements, modules, links, exports, filtering, or dialogs."
---

# Implement a ReqManTool Feature

Implement the requested behavior end to end while preserving the existing local-first architecture.

## Before editing

1. Read `docs/BACKLOG.md` and identify whether the request is already implemented, explicitly backlogged, or new.
2. Find the nearest owning code path:
   - persisted shape: `backend/reqman/models.py` and `backend/reqman/store.py`
   - business behavior: `backend/reqman/service.py`
   - HTTP/WebSocket contract: `backend/reqman/app.py`
   - frontend transport/types: `frontend/src/api/`
   - screen or interaction: `frontend/src/pages/` and `frontend/src/components/`
3. Read the closest tests and one neighboring implementation.
4. State a local hypothesis about the change and a focused check that could disprove it.

## Implementation order

1. Update the domain model only when the persisted contract truly changes. Use dataclasses and backward-compatible `from_dict` defaults.
2. Implement validation and mutation in `ProjectService`. Keep IDs, parent/child rules, ordering, link direction, system attributes, and cleanup centralized there.
3. Add REST request models and routes in `app.py`. Use `_wrap` so `FileNotFoundError` becomes 404 and `ValueError` becomes 400.
4. Add the equivalent WebSocket JSON-RPC operation if the feature is mutating or otherwise exposed over WebSocket. Reuse the same service method.
5. Broadcast `{"event":"changed","project_id":...}` after successful project mutations. Use `root_changed` only for data-directory changes.
6. Add backend tests for success, invalid input, missing resources, persistence/reload, and relevant live-sync behavior.
7. Add typed frontend API functions/types and query invalidation. Reuse the existing `api` client and TanStack Query patterns.
8. Add or update the UI with existing Mantine and Tabler conventions. Include loading, empty, error, disabled, and success states. Keep destructive actions explicit.
9. Update `docs/BACKLOG.md` only when the request changes product status or scope.

## Data and behavior invariants

- Project and module IDs are slugged by the server and suffixed on collision.
- Requirement IDs are server allocated as `<prefix>-<sequence>` and are never manually entered or reused.
- `ObjectText` is the built-in required text attribute.
- Project attributes apply to every module. System/link-mirror attributes cannot be written directly.
- Requirement nesting is within one module; moves must reject self-parenting and descendant cycles.
- Removing a parent promotes its children one level.
- Copy duplicates a whole subtree with new IDs and excludes system/link-mirror attributes.
- Link configurations are directional and server-enforced; link pairs remain the source of truth.
- Purge is a hard delete. Do not add undo behavior unless requested.
- JSON writes remain stable: two-space indentation, dataclass field order, trailing newline.

## Validation loop

After the first edit, run the narrowest focused test immediately. Then run:

```sh
cd backend && pytest -q
cd ../frontend && npm run build && npm run lint
```

If a check fails, repair the same slice and rerun it before expanding the change. Report any unrelated pre-existing failures separately.

## Completion checklist

- Backend rule is centralized and tested.
- REST and WebSocket paths agree.
- Persistence reload works and format remains diff-friendly.
- Frontend types, queries, invalidation, and live sync are aligned.
- Errors and destructive actions are understandable in the UI.
- Focused and broad validation results are recorded.
