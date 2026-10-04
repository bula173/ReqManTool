# ReqManTool Claude Guide

ReqManTool is a local requirements-management application with a Python/FastAPI backend and a React/Vite/TypeScript frontend.

## Repository map

- `backend/reqman/models.py`: persisted dataclasses and schema defaults.
- `backend/reqman/service.py`: business rules shared by REST and WebSocket APIs.
- `backend/reqman/store.py`: JSON-on-disk persistence.
- `backend/reqman/app.py`: FastAPI routes, request validation, WebSocket JSON-RPC, and live-sync broadcasts.
- `backend/tests/`: backend behavior tests.
- `frontend/src/api/`: typed HTTP client, query functions, and API types.
- `frontend/src/components/`: feature dialogs and reusable controls.
- `frontend/src/pages/`: route-level screens.
- `frontend/src/layout/`: application shell.
- `docs/BACKLOG.md`: product scope and implemented/backlog features.

## Working rules

1. Read `docs/BACKLOG.md` and the nearest implementation/tests before changing behavior.
2. Put domain rules in `ProjectService`; do not duplicate them in FastAPI handlers or React components.
3. Preserve the REST and WebSocket behavior parity. A mutating operation must use the shared service and broadcast the appropriate project change event.
4. Preserve the JSON data format, stable field order, two-space indentation, and trailing newline. Treat user data as version-controlled.
5. Requirement IDs, project/module IDs, parent constraints, ordering, link configuration, system attributes, and purge semantics are server-owned rules.
6. Keep frontend API types, query invalidation, loading/error states, and live-sync behavior aligned with backend changes.
7. Do not add authentication, multi-user permissions, or a database unless the user explicitly changes the project scope.
8. Prefer small, focused changes. Do not rewrite unrelated code or erase existing user changes.
9. Add or update focused tests for changed behavior before broad refactoring.
10. Do not commit or create branches unless explicitly requested.

## Validation commands

Backend from `backend/`:

```sh
pytest -q
```

Frontend from `frontend/`:

```sh
npm run build
npm run lint
```

Use the narrowest relevant command first, then run the broader checks when practical.

## Available Claude skills

- `.claude/skills/reqman-feature/SKILL.md`: implement a feature across backend, API, WebSocket, and frontend.
- `.claude/skills/reqman-validation/SKILL.md`: validate a change and report failures with actionable evidence.
