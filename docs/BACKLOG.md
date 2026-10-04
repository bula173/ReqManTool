# ReqManTool product backlog

## Done

- [x] Backend data model: Project → Modules → Requirements, disk-persisted as JSON
- [x] Project-level custom attribute definitions (string/text/integer/boolean/enum)
- [x] Requirement CRUD: add, modify, remove
- [x] Purge: bulk-clear all requirements in a module / project
- [x] Link requirements across modules within a project
- [x] Load project list/contents from disk (stateless, scans on read)
- [x] HTTP REST API (FastAPI) for all of the above
- [x] WebSocket JSON-RPC API (same operations, shared service layer with REST)
- [x] Export project to ReqIF (using the `reqif` package) — plain-text attribute
      values (no XHTML/ENUMERATION datatypes); see `reqman/reqif_export.py`
      docstring/limitations for details
- [x] Live-sync broadcast: mutating REST/WS calls push `{"event":"changed",
      "project_id":...}` to other connected WebSocket clients
- [x] Frontend UI — React + Vite + TS, Mantine components, TanStack Query,
      react-router-dom. Sidebar project/module tree, dynamic requirements
      table (columns from attribute definitions), add/edit/remove/link
      dialogs, purge, ReqIF export button, live sync via WS, light/dark mode.
      See `frontend/README.md`.
- [x] System-generated ids: project/module ids are auto-slugged from their
      name (`-2`/`-3` suffix on collision), never typed by the user.
      Requirement ids are system-allocated as `<module prefix>-<sequence>`
      (e.g. `REQ-0001`), sequence shared per prefix project-wide.
- [x] Inline requirement editing: double-click a cell in the requirements
      table to edit that attribute in place (Enter/blur saves, Escape
      cancels); the "Add Requirement" modal is create-only now.
- [x] Built-in attribute is now just `ObjectText` (required text) — `title`
      and `description` are gone.
- [x] Requirement nesting within a module: any requirement can have children
      (`parent_id`), forming chapters/sections. Add-child and Move (reparent)
      row actions in the table, expand/collapse tree rendering, cycle
      prevention on move, and removing a parent promotes its children one
      level instead of deleting or orphaning them. ReqIF export mirrors the
      nesting as real nested `SPEC-HIERARCHY`.
- [x] Configurable database path via the web UI: `GET/POST /config` lets the
      app switch its data folder at runtime (validated, created if missing),
      persisted to `.reqman/config.json` so it survives a restart (an
      explicit `--dir`/`REQMAN_DATA_DIR` always overrides the saved value).
      A folder icon in the header opens the switcher; switching broadcasts
      `{"event":"root_changed"}` over WebSocket so all connected clients
      clear their cache and reload from the new root.
- [x] DOORS-style link configuration: project-level configurations
      (`name`, source module, target module) define allowed, directional
      link types between two specific modules. Once a project has any
      configuration, all linking goes through one (module + direction
      enforced server-side). Each configuration's pairs are stored
      centrally per project (`<project>/links/<config_id>.json`, the "link
      module" / source of truth) and mirrored as a read-only, auto-managed
      attribute on every linked requirement (`> target_id` / `< source_id`),
      which — since it's modeled as a normal `system=True`
      `AttributeDefinition` — automatically gets its own table column and
      ReqIF `SPEC-RELATION`/`SPEC-RELATION-TYPE` export with no extra
      plumbing. Managed from the project page; the Link dialog is scoped to
      the current module's outgoing configurations.
- [x] JSON files written to disk (project/module/requirement/link-pairs/
      config) now end with a trailing newline for clean git diffs — data is
      meant to be version-controlled by the user, so file formatting is
      kept diff-friendly by design (stable key order via dataclass field
      order + `indent=2`).
- [x] Requirement copy/cut/paste + sibling reordering: `Requirement` gained
      an `order` field (assigned sequentially per parent). `POST
      .../requirements/{id}/reorder` swaps a requirement's order with the
      adjacent sibling (Move Up/Down, no-op at the boundary); `POST
      .../requirements/{id}/copy` deep-duplicates a subtree with new
      system-generated ids, excluding system/link-mirror attributes;
      `PUT .../requirements/{id}/parent` (the existing move endpoint) now
      also appends the moved requirement at the end of its new parent's
      children, and doubles as "cut→paste" since children carry the whole
      subtree with them via `parent_id`. Frontend: right-click a
      requirement row for a context menu (Copy/Cut/Paste as
      child/Move Up/Move Down/Remove); a toolbar Paste button pastes at
      root. Scoped to a single module, matching nesting.
- [x] Folder-picker dialog for the data directory: `GET /fs/browse?path=`
      lists subdirectories of a server-side path (404 on a missing path,
      defaults to the current data directory's parent). Frontend
      `FolderPickerModal.tsx` gives a breadcrumb + clickable folder list;
      `DataDirSettings.tsx` gained a "Browse…" button that fills the
      existing path text field (the field stays editable directly too).
- [x] Export project/module to CSV: `GET /projects/{id}/export/csv`
      (`backend/reqman/csv_export.py`, mirrors the `reqif_export.py`
      duck-typed pattern) — one row per requirement across all modules, one
      column per project attribute (header = attribute name), plus Id/
      Module/Parent. "Export CSV" button next to "Export ReqIF" in
      `ModulePage.tsx`.
- [x] Open-source readiness pass: `LICENSE` (MIT), `docs/ARCHITECTURE.md`
      (system design, data model, disk layout, backend layering, API
      surface, frontend structure, extension guide), rewritten root
      `README.md` and `frontend/README.md` (the latter was still Vite's
      scaffold boilerplate), module-level docstrings added to every
      `backend/reqman/*.py` file that lacked one.
- [x] Linked-requirement navigation, tooltip, named attribute views, and a
      requirement detail panel:
      - Double-clicking a `>`/`<` link badge navigates to that requirement
        (cross-module via `GET /projects/{id}/requirements/{requirement_id}`,
        a project-wide lookup; same-module just selects/scrolls). Hovering
        a badge shows the linked requirement's `ObjectText` as a tooltip
        (`EditableAttributeCell.tsx`'s `LinkBadge`).
      - **Views**: `Project.views: list[View]` (`id, name, attribute_ids`);
        create/list/remove via `/projects/{id}/views`. Purely a rendering
        filter — a view never changes what's stored, only which attribute
        columns `ModulePage.tsx` renders (selector in the toolbar,
        "All attributes" by default).
      - **Requirement detail panel**: clicking a row selects it; a
        right-side panel always shows every project attribute for the
        selected requirement (view-independent), editable the same way as
        table cells.
- [x] Export project to XLSX (Excel): `GET /projects/{id}/export/xlsx`
      (`backend/reqman/xlsx_export.py`, via `openpyxl`) — same
      column/content layout as CSV export (Id/Module/Parent + one column
      per project attribute), one sheet. "Export XLSX" button next to
      "Export CSV" in `ModulePage.tsx`.
- [x] Export project to MS Word (.docx): `GET /projects/{id}/export/docx`
      (`backend/reqman/docx_export.py`, via `python-docx`) — unlike the flat
      CSV/XLSX exports, this reads as a formatted spec document: project
      name as the title, one heading per module, and within each module the
      requirement tree (respecting `parent_id`/`order`) as nested headings
      (depth → heading level, capped at 9) with each requirement's non-empty
      attribute values as bold-label paragraphs underneath. "Export DOCX"
      button next to "Export XLSX" in `ModulePage.tsx`.

## Feature reference: what a mature RM tool (DOORS-class) has

Drawn from IBM DOORS/DOORS Next and general requirements-management practice,
grouped by category. This is the checklist future backlog grooming should be
measured against — not all of it is planned, some is explicitly out of scope
(see notes under each item).

**Structure & content** — module = hierarchical tree of objects ✅,
custom attribute schema per project ✅, object types beyond plain text
(headings/chapters) — modeled as a plain requirement used as a parent ✅,
rich/formatted requirement text (bold/lists/images, not just plain text) ❌,
attribute types beyond string/text/integer/boolean/enum — notably **date**
and **user** (who) ❌, requirement templates (pre-filled attribute sets for
a new requirement) ❌.

**Traceability** — links between modules ✅, link direction enforced by a
configured link type per module pair ✅, **suspect links** (a link is
flagged when either endpoint's content changes after the link was made, so
reviewers know to re-verify it) ❌, **traceability matrix** view (grid of
source × target requirements showing which cells are linked) ❌,
**coverage analysis** (% of a module's requirements with at least one
outgoing/incoming link of a given type — finds orphaned requirements) ❌.

**History & baselines** — per-object change history (who/when/what changed)
❌ — deliberately left to the user's own git history per their explicit
choice (files are diff-friendly by design, see Notes), **baselines**
(named, immutable snapshots of a module at a point in time, comparable to
each other with a diff view) — this is DOORS' formal equivalent of "tag a
commit," genuinely useful even with git underneath since it's project-data-
aware (which requirements changed, not just which files) ❌.

**Views, filtering & search** — sort/filter the requirements table by
attribute value ❌, saved/named views (a stored filter + column set) ✅,
full-text search across a module or project ❌, global find-and-replace ❌.

**Import / export** — ReqIF export ✅ (see limitations noted above), CSV
export ✅, Excel export ✅, Word export ✅, **any import** (ReqIF, CSV) ❌ —
currently one-directional (export only).

**Collaboration & review** — comments/discussion thread per requirement ❌,
formal review/approval workflow (reviewer sign-off, review sets) ❌,
requirement status as a first-class concept (Draft/Reviewed/Approved,
distinct from being "just another enum attribute") — arguably already
achievable today via a user-defined enum attribute, so likely not a
separate feature, just worth a default-attribute-template nudge ❌,
change notifications beyond live table refresh (e.g. "notify me when a
requirement linked to mine changes") ❌.

**Bulk operations & editing ergonomics** — copy/paste and cut/paste a
requirement (subtree) — see below ❌, multi-select + bulk attribute edit
❌, sibling reordering (drag or move up/down) — see below ❌.

**Administration** — user accounts / permissions (who can edit vs. view) ❌
— deliberately out of scope so far (single-user local tool, see Notes),
concurrent-edit protection (two people editing the same requirement at once
— currently last-write-wins, no lock/conflict warning) ❌.

**Reporting / publishing** — printable/publishable formatted document view
of a module (the classic "generate a spec document" DOORS output) ❌.

## Backlog (not started)

Ordered roughly by how directly each was requested / how foundational it is
to the categories above.

- [ ] Import (ReqIF / CSV) — not yet requested, worth asking about when export lands
- [ ] Sort/filter the requirements table by attribute value, plus a simple
      full-text search box (module or project scope). Table-stakes for any
      module with more than a screenful of requirements. Not yet requested.
- [ ] Suspect links: flag a link when either linked requirement's content
      changed after the link was made (e.g. compare a stored
      last-linked-at attribute value/hash against the current one), and
      surface it visually (e.g. a warning color on the `>`/`<` badge). Not
      yet requested — DOORS' core value-add over "just having links."
- [ ] Traceability matrix / coverage view: for a given link configuration,
      show a grid of source × target requirements, and a simple "N of M
      source requirements have at least one link" coverage stat. Not yet
      requested.
- [ ] Baselines: name and snapshot a module (or project) at a point in
      time; compare two baselines to see what changed. Complements (not a
      replacement for) the user's own git history — a baseline is aware of
      requirement-level semantics (added/removed/modified requirement),
      not just file diffs. Not yet requested.
- [ ] Comments/discussion thread per requirement. Not yet requested.
- [ ] Multi-select + bulk attribute edit across several requirements at
      once. Not yet requested.
- [ ] `date` (and possibly `user`) as additional attribute types, alongside
      the existing string/text/integer/boolean/enum. Not yet requested.
- [ ] Printable/publishable document view of a module (formatted export to
      a readable spec document, distinct from the structured ReqIF/CSV/
      Excel/Word exports above). Not yet requested.
- [ ] Authentication / multi-user access control, and concurrent-edit
      protection (detect/warn when two clients edit the same requirement
      at once — today it's last-write-wins). Not yet requested; likely
      stays out of scope for a single-user local tool unless that changes.
- [ ] Git integration: the user manages requirement history via git
      themselves today (files are diff-friendly by design, see Notes).
      Possible in-app conveniences on top of that: show `git status`/diff
      for the current data directory, a "commit" action with a message
      box, or at minimum documentation of the recommended git workflow
      (e.g. `git init` the data directory, `.gitignore` guidance). Requested
      2026-09-16 — scope not yet narrowed down with the user.

## Notes

- Attribute definitions live at the **project** level and apply to every module in it (decided 2026-09-16).
- Requirement ids are unique project-wide so links never need a module qualifier.
- Purge is a hard delete (no trash/undo) — bulk-clears a module or whole project.
- Every module has a required **prefix** (e.g. "REQ"); requirement ids are
  always system-allocated, never user-typed. Purged/removed ids are never
  reused (counters only increase).
- Requirement nesting (chapters/children) is scoped to a single module — a
  requirement's parent must be in the same module. Links (project-wide,
  cross-module) are a separate concept from nesting.
- See `/Users/Marcin/.claude/plans/floating-baking-avalanche.md` for the detailed implementation plan of the "in progress" section.
