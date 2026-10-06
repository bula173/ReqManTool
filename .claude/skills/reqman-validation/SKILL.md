---
name: reqman-validation
description: "Use when checking, testing, reviewing, or preparing a ReqManTool backend/frontend change for completion, including Python tests, FastAPI contracts, WebSocket parity, React builds, linting, and data-format safety."
---

# Validate ReqManTool Changes

Use evidence from the smallest relevant check first, then widen only as needed.

## Scope the check

- Backend domain or persistence change: run the closest `backend/tests/test_service.py` tests, then `cd backend && pytest -q`.
- REST or WebSocket change: run `backend/tests/test_api.py`, then the full backend suite.
- Frontend component/API change: run `cd frontend && npm test`, then `npm run build` and `npm run lint`.
- Cross-stack feature: run focused backend tests, frontend build/lint, then both full suites.

## Review points

Check that:

1. Invalid IDs, unknown attributes, invalid enum values, bad parents, and illegal link directions fail with the expected status/error.
2. A successful mutation persists to disk and survives a fresh store/service reload.
3. REST and WebSocket operations call the same `ProjectService` behavior.
4. Mutations broadcast `changed` only after success, and data-root switches broadcast `root_changed`.
5. Requirement tree operations preserve parent constraints and sibling order.
6. Link cleanup updates both the central link-pair file and mirrored system attributes.
7. Existing JSON files remain readable and newly written files retain stable formatting.
8. The frontend invalidates or refreshes affected queries and handles loading/error/empty states.
9. No unrelated files, dependencies, generated artifacts, or user data were changed.

## Reporting format

Report findings first, ordered by severity, with clickable repository file paths and a concise explanation of impact. Then report:

- checks run and their result;
- assumptions or residual risk;
- changed-scope summary.

Do not claim browser behavior was verified unless a browser test or manual runtime check actually ran.
