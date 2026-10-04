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
- [x] Import CSV into a module: `POST
      /projects/{project_id}/modules/{module_id}/import/csv` (raw body is
      the CSV, `Content-Type: text/csv` required → 415 otherwise, 10 MiB cap
      → 413; returns `{imported, requirement_ids, ignored_columns}`) and
      WebSocket `requirement.import_csv` (`{project_id, module_id, csv}`).
      `backend/reqman/csv_import.py` is the inverse of `csv_export.py`;
      `ProjectService.import_csv` validates all-or-nothing with row-numbered
      errors, allocates ids server-side (the file's Id column is only a key
      for Parent references within the file; parents may come later in the
      file, cycles rejected), treats Module as informational (a file with
      several modules is rejected), ignores system/link-mirror columns so an
      export can be re-imported, rejects unknown columns, type-checks values
      (integer ASCII digits only, boolean variants, exact enum match),
      applies defaults to empty cells, and appends imported roots after the
      existing roots. "Import CSV" button next to "Export CSV" in
      `ModulePage.tsx` opens `ImportCsvModal.tsx`. Related fixes: CSV export
      writes rows depth-first with siblings sorted by `order`, so manual
      ordering survives export → import; sibling `order` is now max+1 in
      add, move, copy and import (was the sibling count, which collided
      after a removal).

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
export ✅, Excel export ✅, Word export ✅, CSV import ✅, ReqIF import ❌.

**Collaboration & review** — comments/discussion thread per requirement ❌,
formal review/approval workflow (reviewer sign-off, review sets) ❌,
requirement status as a first-class concept (Draft/Reviewed/Approved,
distinct from being "just another enum attribute") — arguably already
achievable today via a user-defined enum attribute, so likely not a
separate feature, just worth a default-attribute-template nudge ❌,
change notifications beyond live table refresh (e.g. "notify me when a
requirement linked to mine changes") ❌.

**Bulk operations & editing ergonomics** — copy/paste and cut/paste a
requirement (subtree) ✅, multi-select + bulk attribute edit
❌, sibling reordering (drag or move up/down) ✅.

**Administration** — user accounts / permissions (who can edit vs. view) ❌
— out of scope so far (single-user local tool, see Notes); user handling
requested 2026-10-04, see backlog, concurrent-edit protection (two people editing the same requirement at once
— currently last-write-wins, no lock/conflict warning) ❌.

**Reporting / publishing** — printable/publishable formatted document view
of a module (the classic "generate a spec document" DOORS output) ❌.

## Backlog (not started)

Ordered roughly by how directly each was requested / how foundational it is
to the categories above.

- [ ] Tool version + build version in the UI footer. Requested 2026-10-04.
      One version source (`backend/reqman/__init__.py` `__version__`,
      later the release tag via the distribution item); build id = short
      git commit + build date, injected into the frontend at
      `npm run build` (Vite `define`, falls back to `dev`) and exposed by
      the backend as `GET /version` → `{version, build}` (+ WebSocket
      `version.get`). The app shell footer shows e.g.
      `ReqManTool 0.1.0 · build 6efc9a7 (2026-10-04)` and flags a
      frontend/backend version mismatch.
- [ ] Project templates (JSON) + CENELEC/ERTMS `RBC_Template` + subproject
      tree. Requested 2026-10-04. (1) Templates are JSON files with a
      `format_version`: built-in ones ship in `backend/reqman/templates/`,
      user ones live in the data directory (never listed as projects). A
      template holds attribute definitions, modules (name, prefix), link
      configurations between template modules, and views — no
      requirements. `GET /templates`; creating a project accepts an
      optional `template_id`; "Save as template" turns a project's
      structure into a user template; WebSocket parity for all of it.
      (2) Built-in `RBC_Template` for an ERTMS/ETCS RBC under EN 50126 /
      EN 50128 / EN 50129: modules StRS (stakeholder requirements), SyRS
      (system requirements), SwRS (software requirements), SysTest (system
      test cases), SwTest (software test cases); default link
      configurations SyRS→StRS, SwRS→SyRS, SysTest→SyRS, SwTest→SwRS;
      CENELEC attributes (Object Type, Status, SIL, Safety Related, Hazard
      Ref, Allocation, Verification Method, Source, Rationale, test
      fields) and role views (Requirements, Safety, Test Cases, Review).
      (3) Subprojects: `Project.parent_project_id` (backward-compatible,
      `None` = top level) forms a generic→specific product-line tree per
      EN 50129, e.g. RTE → RBC_GP (generic product) → RBC_GA_NCBIR
      (generic application) → RBC_SA_NCBIR (specific application) →
      RBC_NCBIR; a subproject is typically created from the same template.
      Sidebar shows the tree; cycles and dangling parents rejected
      server-side.
- [ ] Application menu bar. Requested 2026-10-04. A desktop-style menu bar
      in the app shell (`frontend/src/layout/`), built on the existing
      features rather than duplicating their logic: **File** — New project
      (from template, with parent project), Open project folder (the
      existing data-directory switcher / folder picker), Load template
      (create from a template; import a template JSON file, validated
      server-side), Save as template, Import CSV, Export ▸ ReqIF/CSV/XLSX/
      DOCX; **Edit** — Cut, Copy, Paste (as child / at root), Move Up, Move
      Down, Delete on the selected requirement (same actions as the row
      context menu), enabled/disabled by selection and clipboard state,
      with keyboard shortcuts (Ctrl/Cmd+X/C/V, Alt+↑/↓) that stay inactive
      while a text field or inline cell editor has focus; **View** —
      attribute view selector, detail panel toggle, light/dark mode;
      **Help** — keyboard shortcuts, documentation links, About (tool
      version + build, from the version item). Depends on the version and
      template items above.
- [ ] Multiple open modules (tabs), detachable module windows, and
      drag-and-drop linking. Requested 2026-10-04. (1) Tabs: opening a
      module adds a tab to a tab bar listing the open modules (project ·
      module name); switch, close, and reorder tabs; the open-tab list is
      remembered per browser. (2) Detach: a tab can be popped out into its
      own browser window (minimal chrome, same live sync) and re-attached;
      an optional side-by-side split view gives the same result inside one
      window. (3) Drag-and-drop linking: drag a requirement row (or a
      multi-selection, once bulk selection exists) from one module onto a
      requirement in another module — across tabs/split panes or between
      detached windows (HTML5 drag-and-drop with a typed payload of
      project/module/requirement ids). The link set is resolved from the
      project's link configurations between the two modules, in either
      drag direction, and the link is created in the configured direction;
      several matching configurations → the user picks one; **no link
      configuration between the two modules → a warning is shown and no
      link is created**. The server stays the authority (the existing
      link-creation rules reject illegal pairs); duplicates are reported,
      not duplicated. Works across projects once cross-project
      traceability lands.
- [ ] Cross-project traceability in a subproject tree (follows the item
      above). A link configuration may target a module in the project's
      parent (or any ancestor) project, e.g. RBC_SA_NCBIR SyRS → RBC_GA_NCBIR
      SyRS. Requirement ids are only unique per project, so cross-project
      link ends are shown project-qualified (`RBC_GP:SYRS-0001`); link
      pairs stay in the source (child) project; removing a requirement in
      a parent cleans up child link pairs and mirrors; navigation, tooltip
      and exports (ReqIF/CSV/XLSX/DOCX) handle qualified ends. Requested
      2026-10-04 (chosen over a links-free tree).
- [ ] Distribution package + user-space install. Requested 2026-10-04.
      (1) Packaging: a `pyproject.toml` for the backend with a `reqman`
      console script; the built frontend (`npm run build` → `dist/`) is
      bundled inside the Python package and served by FastAPI from the
      same origin (API under its current paths, SPA fallback to
      `index.html`), so the frontend's API/WS base URL defaults to the page
      origin instead of `localhost:8899` (`VITE_*` overrides kept for dev).
      Test-only deps (`pytest`, `httpx`) move out of runtime requirements.
      (2) GitHub Actions: CI on push/PR (backend `pytest`, frontend build +
      lint); on a `v*` tag, build the frontend, build the wheel/sdist, and
      attach them to a GitHub Release. Install with
      `pipx install <release wheel URL>` (or `uv tool install`).
      (3) User-space setup: `reqman init` creates the per-user config file
      and data directory — no root/admin needed. Defaults follow the
      platform: config in `~/.config/reqman/config.json` (Linux, honouring
      `XDG_CONFIG_HOME`), `~/Library/Application Support/ReqManTool/` (macOS),
      `%APPDATA%\ReqManTool\` (Windows); data in the matching user data dir
      (or a path the user chooses at `init`). Optional
      `reqman service install|uninstall` registers a per-user background
      service (systemd `--user` unit on Linux, launchd agent on macOS).
      (4) Own JSON config file: replaces today's repo-relative
      `.reqman/config.json`. Keys: `data_dir` (storage root), `host`,
      `port`, `cors_origins`; precedence CLI flag > env var
      (`REQMAN_DATA_DIR`, `REQMAN_CONFIG_FILE`, …) > config file > platform
      default. The in-app data-folder switcher writes `data_dir` back to the
      same file (stable key order, two-space indent, trailing newline).
      Running from a git checkout (`etc/start*.sh`) keeps working. Default
      bind stays `localhost`.
- [ ] Import ReqIF (the CSV slice is done, see Done). Reuse the CSV
      import's all-or-nothing validation (collect every error, write
      nothing on failure) and its server-allocated-id approach (the file's
      identifiers are only keys for resolving parents within the file).
      Open issue carried over from CSV: an attribute named Id/Module/Parent,
      or two attributes sharing a name, breaks re-import of a project's own
      CSV export — product decision needed: reject such names in
      `add_attribute_definition`, or match those columns by attribute id.
- [ ] User handling. Requested 2026-10-04; scope not yet narrowed down
      with the user. Changes the project scope (today: single-user local
      tool, no auth), so `.claude/CLAUDE.md` rule 7 must be lifted before
      implementation. Candidate pieces, roughly in dependency order:
      (1) identity — preferably taken from a fronting reverse proxy (e.g.
      Cloudflare Access: verify the `Cf-Access-Jwt-Assertion` JWT, read the
      user's email) rather than built-in accounts/passwords, with a
      configurable "local single user" fallback; (2) attribution —
      server-maintained `created_by`/`modified_by` (+ timestamps) system
      attributes on requirements, and identity in live-sync events;
      (3) roles per project (viewer / editor / admin) enforced in
      `ProjectService`, with REST/WebSocket parity; (4) concurrent-edit
      protection — a per-requirement version/ETag, stale writes rejected
      with 409 and a conflict prompt in the UI instead of today's
      last-write-wins; (5) `user` attribute type (assignee/owner), see the
      `date`/`user` item below.
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
