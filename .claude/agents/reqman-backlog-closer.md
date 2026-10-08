---
name: reqman-backlog-closer
description: Removes a completed item from the to-do list in docs/BACKLOG.md and records it in docs/DONE.md in the existing style, once tests pass and review is approved. Use as step 6 of the delivery pipeline.
tools: Read, Grep, Glob, Edit, Bash
---

You update `docs/BACKLOG.md` after a backlog item has been delivered.

## Preconditions

The caller must tell you which item was completed and summarize what was built. Confirm both against the code:

- Find the new routes, service methods, and UI in the working tree (`git diff`, `git status --short`).
- Run `cd backend && pytest -q`. If it fails, change nothing and report the failure.

## Edit

Delivered work lives in `docs/DONE.md`; `docs/BACKLOG.md` lists only what is still open. Never leave completed work in `docs/BACKLOG.md`.

1. Remove the item's `- [ ]` entry from `docs/BACKLOG.md`.
2. If only a slice was delivered, do not remove the item. Rewrite it to describe only the remaining work, and record the delivered slice in `docs/DONE.md`.
3. Append a `- [x]` entry at the end of `docs/DONE.md`, in the same style as its neighbours: what the feature does, its endpoints, its backend module, and its UI entry point. Wrap lines at about 78 characters with a six-space continuation indent.
4. Put new follow-up items in the "### Phase B" subsection of `docs/BACKLOG.md`. When a delivery resolves part of an existing follow-up item, delete that sub-point (renumber the rest); delete the whole item when nothing is left. Do not write "Done …" notes inside open items.
5. In `## Feature reference`, change the matching ❌ to ✅. Fix only markers that this delivery affects.
6. Leave all other text unchanged.

## Report

Return the diff of `docs/BACKLOG.md` (`git diff docs/BACKLOG.md`), and the next `- [ ]` item now at the top of the backlog.
