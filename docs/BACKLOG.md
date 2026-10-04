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
- [x] Tool version + build version in the UI footer: one version source,
      `__version__` in `backend/reqman/__init__.py` (the frontend reads it
      from that file at build time in `vite.config.ts`, fallback
      `unknown`). `GET /version` and WebSocket `version.get` (read-only, no
      broadcast) return `{version, build}` via `backend/reqman/version.py`;
      the backend build id is `REQMAN_BUILD` if set, else
      `<short commit> (<commit date>)` from git (cached per process,
      pre-warmed at startup, falls back to `dev`). The frontend build id is
      injected via Vite `define` (`REQMAN_BUILD` override, else short
      commit + build date on `npm run build`, `dev` on the dev server or
      without git). `VersionFooter.tsx` in the `AppLayout.tsx` footer shows
      `ReqManTool <version> · build <build>`, the backend version/build in
      a tooltip, a "Version mismatch" badge when frontend and backend
      versions differ (builds are not compared), and "(backend version
      unavailable)" when `/version` fails; the version is refetched on
      WebSocket reconnect. Not covered: tying the version to the release
      tag (left to the distribution item), a `-dirty` marker on build ids,
      frontend unit tests (no test runner in the repo).
- [x] Project templates (JSON) + CENELEC/ERTMS `RBC_Template` + subproject
      tree. Templates are JSON files with `format_version` 1
      (`backend/reqman/templates.py`): attribute definitions (built-in
      `object_text` implicit), modules (`key`, `name`, `prefix`,
      `description`), link configurations between module keys and views
      (`link:<key>` = that link's mirror attribute) — no requirements.
      Unknown keys are ignored, other `format_version` values rejected. A
      template is validated on load by building the project in memory with
      the normal service helpers, plus template rules (duplicate module
      keys/names, link keys, view names, attribute ids; unknown module
      keys; no `system`/`link:` attributes). Built-in
      `backend/reqman/templates/rbc_template.json` (`RBC_Template`): modules
      StRS/SyRS/SwRS/SysTest/SwTest, links SyRS→StRS, SwRS→SyRS,
      SysTest→SyRS, SwTest→SwRS, CENELEC attributes, views Requirements/
      Safety/Test Cases/Review. Template ids are `builtin:<stem>` /
      `user:<stem>`; user templates live in `<data dir>/_templates/` (it and
      `.reqman-staging` are never listed as projects). `GET /templates`
      (WS `template.list`) lists both, an invalid user template with an
      `error`. `POST /projects` (WS `project.create`) takes optional
      `template_id` and `parent_project_id`; creation from a template is
      all-or-nothing (built in `.reqman-staging/`, moved in with one
      rename). `POST /projects/{id}/save-as-template` (WS
      `project.save_as_template`) writes the structure as a user template
      (name clash → 409, never overwrites) and broadcasts
      `templates_changed`. Subprojects: `Project.parent_project_id`
      (written only when set, so existing `project.json` files stay
      byte-identical), `PUT /projects/{id}/parent` (WS
      `project.set_parent`); unknown parents, self and cycles rejected;
      hand-edited dangling/cyclic parents shown as top level without
      rewriting; project ids that are not a safe single path component are
      never listed or loaded. Frontend: template + parent pickers in
      `NewProjectModal.tsx`, `SaveAsTemplateModal.tsx`,
      `ProjectParentModal.tsx`, "New subproject", project tree in
      `Sidebar.tsx` (`projectTree.ts`); the template list refreshes on
      `templates_changed`. Not covered: duplicate view names are allowed in
      projects but rejected in templates, and user templates with a UTF-8
      BOM are rejected (both product decisions); blank project creation can
      still write into an existing non-project folder of the same name
      (pre-existing); modules are listed in id order, not template order
      (no persisted module order); cross-project links, module types and
      the menu bar remain separate backlog items; no frontend unit tests
      (no test runner in the repo).

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
a new requirement) ❌, attribute schema evolution (rename/retire enum
choices with migration, delete attributes, rename/delete modules/projects)
and a multi-valued enum ❌, requirement reuse from an ancestor project with
origin/drift tracking ❌.

**Traceability** — links between modules ✅, link direction enforced by a
configured link type per module pair ✅, **suspect links** (a link is
flagged when either endpoint's content changes after the link was made, so
reviewers know to re-verify it) ❌, **traceability matrix** view (grid of
source × target requirements showing which cells are linked) ❌,
**coverage analysis** (% of a module's requirements with at least one
outgoing/incoming link of a given type — finds orphaned requirements) ❌,
**impact analysis** (transitive upstream/downstream walk) ❌, source/test-
code tag traceability ❌, versioned reference-document citations (e.g.
UNISIG subsets) ❌, safety registers — hazard log with closure status, SRAC
register propagated down the project tree ❌.

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
export ✅, Excel export ✅, Word export ✅, CSV import ✅, ReqIF import ❌,
test-result import (JUnit XML / Robot Framework) ❌.

**Collaboration & review** — comments/discussion thread per requirement ❌,
formal review/approval workflow (reviewer sign-off, review sets) ❌,
requirement status as a first-class concept (Draft/Reviewed/Approved,
distinct from being "just another enum attribute") — arguably already
achievable today via a user-defined enum attribute, so likely not a
separate feature, just worth a default-attribute-template nudge ❌,
change notifications beyond live table refresh (e.g. "notify me when a
requirement linked to mine changes") ❌, rule-based consistency/quality
checks (conditional-required attributes, link counts, weak words) ❌.

**Bulk operations & editing ergonomics** — copy/paste and cut/paste a
requirement (subtree) ✅, multi-select + bulk attribute edit
❌, sibling reordering (drag or move up/down) ✅, soft delete with a
"Show deleted" toggle and undelete ❌.

**Administration** — user accounts / permissions (who can edit vs. view) ❌
— out of scope so far (single-user local tool, see Notes); user handling
requested 2026-10-04, see backlog, concurrent-edit protection (two people editing the same requirement at once
— currently last-write-wins, no lock/conflict warning) ❌, role sign-off
records with an independence check (EN 50128 roles) ❌, tool qualification
evidence (EN 50128 §6.7) ❌.

**Reporting / publishing** — printable/publishable formatted document view
of a module (the classic "generate a spec document" DOORS output) ❌,
custom report templates ❌.

## Backlog (not started)

Ordered roughly by how directly each was requested / how foundational it is
to the categories above.

- [ ] Data format versions, persisted module order, and template/view
      consistency fixes. Requested 2026-10-04. (1) Format versions:
      `project.json` and the app config file (`.reqman/config.json`, later
      the per-user config of the distribution item) carry an integer
      `format_version` as their first key; module, requirement and
      link-pair files are governed by their project's version. Files
      without the key are version 1 (today's format) and keep loading
      unchanged; the loader upgrades older versions in memory through
      explicit, tested migration steps and writes the current version on
      the next save; a file newer than the running tool supports is
      rejected with a clear "written by a newer ReqManTool" error instead
      of being misread or overwritten. Templates keep their own
      `format_version`. (2) Module order: projects persist an explicit
      module order (new modules appended; templates keep their declared
      order, so `RBC_Template` shows StRS → SyRS → SwRS → SysTest → SwTest);
      move up/down for modules in the UI, REST + WebSocket. (3) Projects
      reject duplicate view names (case-insensitive, like templates).
      (4) User template files saved with a UTF-8 BOM are accepted.
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
      version + build, from the version item).
- [ ] Left-click and right-click menus everywhere. Requested 2026-10-04.
      Two distinct menus, adapted to what was clicked: **left click** opens
      a navigation/view menu (open, open in new tab, detach, show links,
      go to linked requirement, show in tree, …); **right click** opens an
      editing menu (cut/copy/paste, add child/sibling, move up/down, move
      to folder, delete, link to…, rename, properties, …). Places and
      their entries: requirement rows and cells (incl. `>`/`<` link
      badges: go to / remove link), sidebar tree (project, subproject,
      module folder, module: new module, new subproject, rename,
      references, save as template, open in tab, detach), column headers
      (sort, filter, hide, add to view, attribute properties), and module
      tabs (close, close others, detach, split view). One shared menu
      registry per element type so the menu bar, both context menus and
      keyboard shortcuts call the same actions; entries the place does not
      support are hidden, unavailable ones disabled. Left click still
      selects; double-click keeps inline editing; Esc / outside click
      closes; keyboard-accessible (Shift+F10 / menu key opens the editing
      menu). Builds on the menu bar item; tab, folder and reference
      entries appear as those items land.
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
- [ ] Module types + traceability matrix, verification report and test
      execution report. Requested 2026-10-04 (absorbs the former
      "traceability matrix / coverage view" item). (1) Module types:
      `Module.type` ∈ `requirements` | `test_cases` | `test_execution`
      (backward-compatible default `requirements`), chosen at creation,
      shown as an icon in the sidebar/tabs, set by templates. Attributes
      gain an optional applicability list of module types (default: all),
      so test fields stop appearing as columns in requirement modules and
      vice versa. Link configurations are checked for sensible type pairs
      (requirements→requirements, test_cases→requirements,
      test_execution→test_cases). (2) `RBC_Template` update: StRS/SyRS/SwRS
      = requirements, SysTest/SwTest = test_cases, new SysTestExec/SwTestExec
      = test_execution with links "Executes SysTest" / "Executes SwTest" and
      execution attributes (Result: Passed/Failed/Blocked/Not Run, Executed
      By, Execution Date — needs the `date` type item, Build/SW Version
      Under Test, Evidence/Log Ref). (3) Traceability matrix: pick a link
      configuration (or a chain, e.g. StRS→SyRS→SwRS→SwTest→SwTestExec) and
      get a source × target grid plus coverage stats ("N of M covered"),
      orphan and unverified requirement lists; export XLSX/CSV.
      (4) Verification report: for a requirements module, each
      requirement's covering test cases, their latest execution result,
      and a status (verified / failed / not run / not covered), with
      totals and % — evidence-style output for EN 50128/EN 50129
      verification. (5) Test execution report: for a test_execution module
      (one test campaign / build), executed vs. planned test cases,
      pass/fail/blocked counts, failed tests with the requirements they
      affect, version/build under test, executor and date. Reports render
      in the UI and export to DOCX/XLSX (reusing the existing exporters'
      patterns); computed by `ProjectService` (read-only, REST + WebSocket).
      (6) Result import (feature scout, 2026-10-04): JUnit XML / Robot
      Framework results import into a test_execution module, matched to
      test cases on a test-id attribute, plus a computed read-only
      "Verification status" system attribute on requirements.
- [ ] Project references + cross-project traceability. Requested
      2026-10-04 (chosen over a links-free tree; widened the same day from
      "ancestors only" to explicit references). (1) Project references: a
      project declares which other projects it references
      (`Project.references`, list of project ids; the parent project is
      referenced implicitly), managed from the project page, REST +
      WebSocket; self-references and dangling ids rejected; a referenced
      project can be anything — an ancestor, a sibling, or a shared
      project such as a UNISIG subset library. (2) Cross-project links: a
      link configuration's target module may live in any referenced
      project (e.g. RBC_SA_NCBIR SyRS → RBC_GA_NCBIR SyRS, or SyRS → a
      shared SUBSET-026 project), direction still enforced server-side.
      Requirement ids are only unique per project, so cross-project link
      ends are shown project-qualified (`RBC_GP:SYRS-0001`); link pairs stay
      in the source (referencing) project; removing a requirement or
      dropping a reference cleans up dependent link pairs and mirrors (or
      is refused while links exist — to be decided at implementation);
      navigation, tooltip, drag-and-drop linking and exports
      (ReqIF/CSV/XLSX/DOCX) handle qualified ends.
- [ ] Module folders. Requested 2026-10-04. Modules inside a project can be
      organised into nested folders (create, rename, delete-empty, move a
      module or folder into another folder, ordering within a folder),
      shown as a tree in the sidebar and in module pickers (link
      configuration dialogs, drag-and-drop, reports). Folders are purely
      organisational: module ids, requirement ids and links are
      unaffected. Persisted in `project.json` as an extension of the
      persisted module order (from the format-version item), with a format
      migration; templates may declare folders (e.g. `RBC_Template`:
      Requirements/ StRS SyRS SwRS, Tests/ SysTest SwTest).
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
- [ ] Consistency/quality checks ("req lint"). Declarative rules in
      `project.json`: conditional-required (Safety Related ⇒ SIL, Hazard
      Ref, Verification Method), link counts per link configuration (every
      SwRS ≥1 → SyRS unless Derived + Rationale), child SIL ≥ parent SIL,
      and a weak-word list (TBD, should, may, and/or, "as appropriate").
      `GET /projects/{id}/checks` + WebSocket `checks.run` return findings
      with severity; a Checks panel jumps to each requirement; usable as a
      CI / git pre-commit gate; `RBC_Template` ships a CENELEC rule set.
      Suggested 2026-10-04 (feature scout, seen in Sphinx-Needs, DOORS Next
      RQA, Jama Advisor, StrictDoc, Doorstop). Not yet requested.
- [ ] Impact analysis. From the selected requirements, walk upstream/
      downstream links transitively to a chosen depth, across modules (and
      the project tree once cross-project links exist); the result lists
      each reached requirement with distance, SIL, Safety Related and
      linked tests. Computed, not stored. Export CSV/XLSX/DOCX with a
      change-reference field, as the EN 50128 §6.6 modification/change-
      control record. Suggested 2026-10-04 (feature scout, seen in Jama
      impact analysis, ReqView multi-level traceability). Not yet requested.
- [ ] SRAC register + propagation through the generic→specific project
      tree (EN 50129). An SRAC module in a GP/GA project; descendants get
      an "Inherited SRACs" view where each SRAC gets a per-child
      disposition: Fulfilled (requires a cross-project link), Transferred
      (re-exported) or Not applicable (requires a justification), stored in
      `<project>/srac_closure.json`. A closure report; open SRACs become
      check findings. Depends on templates/subprojects and cross-project
      links. Suggested 2026-10-04 (feature scout, seen in no tool built-in;
      closest ReqView Linked Projects; EN 50129 safety-case practice). Not
      yet requested.
- [ ] Hazard log module + closure status. `RBC_Template` gains a HAZ
      module (cause, consequence, severity/frequency, risk class, THR/SIL,
      status Open/Mitigated/Closed/Transferred) and a "Mitigates" link
      configuration SyRS→HAZ, replacing the free-text Hazard Ref. A
      computed closure-evidence attribute (are all mitigating requirements
      verified?) and a check rule for "Closed with unverified
      mitigations". Suggested 2026-10-04 (feature scout, seen in
      Codebeamer risk management, ReqView FMEA template). Not yet requested.
- [ ] Reference specifications with versions (UNISIG subsets). A
      per-project registry of reference documents (e.g. SUBSET-026 v3.6.0,
      SUBSET-037 v3.2.0, SUBSET-039, SUBSET-098) and a `reference`
      attribute type holding (document, clause), shown as
      `SUBSET-026 3.6.0 §3.5.3.7`. Bumping a document's version flags every
      citing requirement for re-review via the suspect-link mechanism; a
      "references by document" report; optionally a read-only external
      module of imported clauses. Suggested 2026-10-04 (feature scout, seen
      in Sphinx-Needs needimport/external needs, ReqView Linked Projects;
      ERA B3R2/B4R1 spec lists). Not yet requested.
- [ ] Role sign-off records + independence check (EN 50128 roles). A
      record = subject (requirement/module/baseline), content hash, role
      (Designer/Verifier/Validator/Assessor…), signer, timestamp, verdict,
      comment, stored in `<project>/signoffs/`; it goes stale automatically
      when the content hash changes. A per-project independence matrix is
      evaluated as check findings — reporting only, not permissions.
      Identity typed in, or from Cloudflare Access once user handling
      exists. Suggested 2026-10-04 (feature scout, seen in Doorstop reviewed
      fingerprints, Codebeamer reviews, Polarion approvals). Not yet
      requested.
- [ ] Requirement reuse from an ancestor project (branch with origin
      tracking). Copy a module or subtree from an ancestor into a
      descendant project with new ids, storing `origin {project_id,
      requirement_id, content_hash}`; a drift badge shows when the origin
      changed, with diff / pull update / mark as intentional deviation.
      Suggested 2026-10-04 (feature scout, seen in Polarion branched
      documents, ReqView Linked Projects). Not yet requested.
- [ ] Soft delete. Remove marks a requirement `deleted` (optional reason /
      CR id) instead of deleting its file; a "Show deleted" toggle,
      Undelete, and exports that mark or skip deleted requirements. Purge
      stays the hard delete. Suggested 2026-10-04 (feature scout, seen in
      DOORS soft delete, ReqView, Doorstop `active`). Not yet requested.
- [ ] Source-code and test-code tag traceability. Scan a configured local
      code root read-only for tags such as `@req SWRS-0012`; SwRS gets
      "Implemented in" and "Unit tests" system attributes, with coverage
      counts and an orphan-tag list. Suggested 2026-10-04 (feature scout,
      seen in StrictDoc @relation, Doorstop references). Not yet requested.
- [ ] Attribute schema evolution + multi-valued enum. Edit/rename
      attribute definitions; add/rename/retire enum choices with migration
      across all requirement files; delete non-system attributes after a
      confirmation listing affected requirements; rename/delete modules and
      projects (no such routes today); a `multi_enum` type. Pairs with the
      `date`/`user` type item. Suggested 2026-10-04 (feature scout, seen in
      ReqView custom attributes). Not yet requested.
- [ ] Tool qualification evidence pack (EN 50128 §6.7, likely class T2) —
      a process item, not a feature. If ReqManTool output (coverage,
      verification status, check results) is used as certification
      evidence, it needs documented tool requirements, version-pinned
      releases, and a regression-test evidence report generated from
      `backend/tests/`. Depends on the version footer and distribution
      items. Suggested 2026-10-04 (feature scout, seen in no tool; EN 50128
      §6.7 practice). Not yet requested.
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
      Excel/Word exports above), generalized into custom report templates
      (merged feature-scout candidate, 2026-10-04): a user template picks
      modules, attributes, filters and layout, so the spec document, the
      traceability/verification reports and project-specific deliverables
      share one mechanism. Not yet requested.
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
