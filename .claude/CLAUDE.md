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
7. Authentication, users, roles and edit locks belong to the **server edition** only (backlog item "Server edition"); the **local edition** must keep working with no login. Never add a database: persistence stays JSON files + one git repo per project.
8. Prefer small, focused changes. Do not rewrite unrelated code or erase existing user changes.
9. Add or update focused tests for changed behavior before broad refactoring.
10. Commit each completed backlog item locally as its own commit (stage paths explicitly; never `rbc/`-style user data or `CLAUDE.original.md`). Never push, and do not create branches, unless the user explicitly asks.

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

## Delivery pipeline agents

Subagents in `.claude/agents/`, run in this order. The to-do list is `## Backlog (not started)` in `docs/BACKLOG.md`.

1. `reqman-implementer`: implement the next backlog item (or a named one).
2. `reqman-test-writer`: write unit/API tests for the uncommitted change.
3. `reqman-tester`: run pytest, build, and lint; report failures (read-only).
4. `reqman-reviewer`: review the diff and return ranked findings (read-only).
5. `reqman-fixer`: fix reviewer or tester findings; then rerun `reqman-tester`.
6. `reqman-backlog-closer`: move the delivered item from the backlog to `Done`.
7. `reqman-feature-scout`: research DOORS-class tools for new feature ideas (read-only).
8. `reqman-backlog-groomer`: add the accepted ideas to the backlog.

`reqman-orchestrator` loops agents 1–8: implement → tests → test → review → fix until green and approved (max 3 rounds) → close; it scouts and grooms when the backlog runs dry. It delivers one item per run unless commits are requested. Run it as the main session with `claude --agent reqman-orchestrator`, or ask the main session to "use reqman-orchestrator".
