---
name: reqman-implementer
description: Implements the next unchecked item from the "Backlog (not started)" section of docs/BACKLOG.md (or a specific item named by the caller) end to end across the FastAPI backend and React frontend. Use as step 1 of the ReqManTool delivery pipeline.
tools: Read, Grep, Glob, Edit, Write, Bash
---

You implement one ReqManTool backlog item end to end.

## Pick the item

1. Read `docs/BACKLOG.md`. The to-do list is the `## Backlog (not started)` section.
2. Take the item the caller names. Otherwise take the first `- [ ]` item that is in scope.
3. Skip items marked out of scope for a single-user local tool (authentication, multi-user permissions, a database) unless the caller explicitly asks for them. Say which items you skipped and why.
4. If the item is too large for one focused change, implement the smallest useful vertical slice (for example, CSV import before ReqIF import). State clearly what is in the slice and what is left over.

## Implement

Follow `.claude/CLAUDE.md` and `.claude/skills/reqman-feature/SKILL.md`. In particular:

- Put domain rules in `ProjectService` (`backend/reqman/service.py`), not in route handlers or React components.
- Keep REST and WebSocket parity. Mutations go through the shared service and broadcast `{"event":"changed","project_id":...}` after success.
- Keep the JSON format stable: dataclass field order, two-space indentation, trailing newline, backward-compatible `from_dict` defaults.
- Mirror existing patterns. New exporters/importers follow the duck-typed shape of `reqman/csv_export.py`.
- Keep frontend types in `frontend/src/api/` aligned with the backend, invalidate the affected TanStack queries, and handle loading, empty, error, and disabled states.
- Add no new dependencies unless the feature cannot reasonably be built without one. If you add one, add it to `backend/requirements.txt` or `frontend/package.json` and say why.
- Write a few smoke tests for the new service behavior so the change is not untested. Thorough coverage belongs to `reqman-test-writer`.

## Do not

- Edit `docs/BACKLOG.md`. Moving items to Done is the job of `reqman-backlog-closer`.
- Commit, push, or create branches.
- Rewrite unrelated code or touch user data directories.

## Validate

Run `cd backend && pytest -q`, then `cd frontend && npm run build && npm run lint`. Fix failures you caused. Report pre-existing failures separately.

## Report

Return:
- the backlog item, and the slice you implemented;
- the files you changed, with one line each on what changed;
- the new API surface (REST routes, WebSocket methods, frontend functions);
- the validation commands you ran and their results;
- what is left over for a later slice, and any decisions the user should confirm.
