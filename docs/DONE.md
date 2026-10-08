# ReqManTool — delivered features

Completed backlog items, moved out of `docs/BACKLOG.md` (2026-10-08).
Newest entries are appended at the end.

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
- [x] Data format versions, persisted module order, and template/view
      consistency fixes. (1) Format versions: `backend/reqman/formats.py`
      holds the current version per file kind (project and config both at
      1) and an explicit migration registry (`migrations[n]` upgrades
      n→n+1, applied in memory). `format_version` is the first key of
      `project.json` and `.reqman/config.json`; a missing key means
      version 1; module, requirement and link-pair files are governed by
      their project's version; templates keep their own `format_version`.
      Loading never rewrites a file; the version is written on the next
      real save. A newer file is rejected with "written by a newer
      ReqManTool … the file was not changed" (REST 409, WebSocket -32602)
      and never overwritten: every project-file read/write checks
      `project.json`'s version (cached by mtime/ctime/size/inode); a newer
      config refuses startup and `POST /config`; creating a subproject
      under, or moving a project under, a newer project is rejected
      (prevents an unseen cycle). (2) Module order: optional
      `module_order` in `project.json` (written last, only when non-empty;
      no format bump, since pre-versioning tools cannot be protected
      anyway); projects without it use id order; unknown entries are
      dropped and unlisted modules appended in id order. New modules are
      appended; projects from templates keep the template's declared order
      (`RBC_Template`: StRS → SyRS → SwRS → SysTest → SwTest);
      save-as-template and exports follow it. Move up/down: `POST
      /projects/{project_id}/modules/{module_id}/reorder`
      (`{"direction":"up"|"down"}`) and WebSocket `module.reorder`,
      broadcast `changed`; up/down buttons on the module cards in
      `ProjectPage.tsx`. `docs/ARCHITECTURE.md` documents the planned
      evolution: module folders turn `module_order` into ordered
      module/folder entries via a PROJECT_FORMAT 1→2 migration, and any
      new persisted key from now on needs a format bump. (3) Views:
      duplicate view names in a project are rejected case-insensitively
      after trimming, blank names rejected, names stored trimmed (REST 400,
      WebSocket -32602). (4) Templates: user and built-in template files
      with a UTF-8 BOM are accepted. Backend tests 510 → 615. Not covered
      (see backlog): one project written by a newer tool fails the whole
      project list; data files are not read as `utf-8-sig`; no view rename
      or module delete; module move up/down is not in the sidebar.
- [x] Application menu bar + shared action registry. Desktop-style menu
      bar in the app shell (`frontend/src/layout/MenuBar.tsx`): **File** —
      New project (from template, with parent), Open project folder, Load
      template ▸ (create from template / import template file), Save as
      template, Import CSV, Export ▸ ReqIF/CSV/XLSX/DOCX; **Edit** — Cut,
      Copy, Paste as child, Paste at root, Move Up, Move Down, Delete;
      **View** — attribute view ▸, detail panel toggle (remembered per
      browser), light/dark; **Help** — keyboard shortcuts (dialog generated
      from the registry), documentation links, About (tool version +
      build). One action registry in `frontend/src/actions/` (id, label,
      shortcuts, `isAvailable`/`run` for a context; targets keyed by
      element type, `app` and `requirement` today; menus are data) drives
      the menu bar, the keyboard shortcuts, the requirement row context
      menu and the toolbar buttons (exports, paste at root, import CSV, new
      subproject) — the base for the "Left-click and right-click menus
      everywhere" item. Shortcuts Ctrl/Cmd+X/C/V, Ctrl/Cmd+Shift+V (paste
      at root), Alt+↑/↓; inactive in inputs, textareas, selects,
      contenteditable, the inline cell editor, open menus and dialogs.
      Ctrl/Cmd+V pastes as child of the selection, or at root only when
      nothing is selected; paste as child onto the copied requirement
      itself is unavailable. The clipboard is per data folder and is
      cleared, with the selection, on a data-folder switch. Keyboard
      access: `role="menubar"`, roving tabindex, Arrow Left/Right/Home/
      End/Down; Escape and submenu ArrowLeft return focus (workaround for
      a Mantine `focusParentItem` bug). Template import: `POST
      /templates/import` (raw JSON body; not `application/json` → 415;
      over 1 MiB of UTF-8 → 413; invalid UTF-8, invalid template, an
      unencodable lone surrogate or nesting deeper than 100 levels → 400)
      and WebSocket `template.import` (-32602), via
      `ProjectService.import_template`: validated by the existing template
      validation and saved as a user template `_templates/<slug>.json` in
      standard form (never overwrites; a name already used by any template
      → 409), broadcast `templates_changed`. Template listing no longer
      fails with 500 on such files on disk (listed as invalid). Frontend:
      `ImportTemplateModal.tsx`, `KeyboardShortcutsModal.tsx`,
      `AboutModal.tsx`, `AppDialogs.tsx`. Backend tests 615 → 692. Not
      covered (see backlog): paste as sibling; Export ▸ is enabled only
      while a module is open; the templates list is fetched on every app
      load; the page-local Add child / Link / Move to parent dialogs are
      not registry actions yet; no detail-panel (collapsible-panels item),
      Delete or F10/Alt shortcut; no user-template management; no frontend
      unit tests (no test runner in the repo).
- [x] Project list with unloadable projects + `utf-8-sig` data files.
      (1) Stubs: `GET /projects` and WebSocket `project.list` no longer
      fail as a whole. A project whose `project.json` cannot be loaded
      (newer `format_version`, invalid UTF-8 or JSON, not an object,
      invalid `format_version`, missing key or wrong shape, unreadable
      file, `project.json` that is a directory) is listed as a read-only
      stub `{id, name, error}` (`ProjectStub` in `service.py`); `name` is
      the stored name if usable, else the id; its stored parent is
      ignored, so it is always top-level, but readable subprojects stay
      under it; a folder removed mid-listing is skipped; a data-root
      folder the process cannot search is listed as a stub only if its
      name is a server slug (`PROJECT_SLUG_PATTERN`, no dot-names, so
      `lost+found`/`.Spotlight-V100` stay hidden). Stubs are fully
      read-only: every read/write of the project's module, requirement
      and link-pair files runs the format check first; it shares
      `_project_from_data` (upgrade + `Project.from_dict`) with loading,
      so it fails exactly when loading would, and is cached only on
      success. Opening a stub, or any call on it (modules, requirements,
      links, views, attributes, purge, missing modules/requirements),
      fails with the stub error: newer → REST 409, otherwise REST 400;
      WebSocket -32602; no broadcast. An unreadable file reports "Project
      '<id>' has an unreadable project.json: <strerror>" with no absolute
      path. `purge_project`/`purge_module` load the project first
      (missing project → 404 / -32001; a missing module in a valid
      project is still `{"purged": 0}`). Frontend: `ProjectStub`/
      `ProjectListEntry`/`isProjectStub` types, `useProjects()` returns
      list entries; the sidebar `StubProjectNode` (greyed, warning icon,
      error inline plus a tooltip on hover/focus/touch) is a button to
      `/projects/{id}`, where `ProjectPage` shows "Failed to load
      project: …", with no modules and no New module entry; parent
      pickers list stubs disabled ("(cannot be opened)"); `HomePage`
      opens the first loadable project, or shows an alert when every
      project is a stub; `ProjectPage` shows a stub parent without a
      link. (2) Encoding: project, module, requirement and link-pair
      files are read as `utf-8-sig` via `_read_json` (config, templates
      and template import already were); writes stay plain UTF-8 without
      BOM in the unchanged format, `config.py` writes with explicit
      utf-8; `_check_project_format` no longer swallows JSON errors, so a
      malformed `project.json` is never treated as version 1. (3) Found
      in review: an over-long project or module name (id longer than the
      filesystem name limit, `os.pathconf` `PC_NAME_MAX`, fallback 255)
      hung the server in `_unique_slug` or gave a 500; now REST 400 /
      WebSocket -32602 with nothing written, ids are never truncated, and
      the slug collision search is bounded at 10 000 candidates.
      `docs/ARCHITECTURE.md` updated. Backend tests 692 → 756 (new
      `backend/tests/test_project_stubs_and_encoding.py`). Not covered
      (see backlog): the server still accepts an invalid-JSON (non-newer)
      stub as the parent when creating or moving a project, while the UI
      pickers disable all stubs (open decision); over-long link
      configuration, user template and template module names still give
      a 500; Windows MAX_PATH (260) is not covered by the per-name length
      check; sidebar entries for normal projects and modules are `<a>`
      without href, so they cannot be reached with Tab (pre-existing); no
      frontend unit tests (no test runner in the repo).
- [x] Git integration, slice 2a: per-project repos, auto-commit and
      per-requirement history (baselines and the project Git panel are
      slice 2b, see backlog). New `backend/reqman/git_repo.py` (git CLI
      via subprocess, no new dependency) and `backend/reqman/history.py`
      (pure message, diff and blame functions). (1) State: `GET
      /projects/{id}/git` and WebSocket `project.git_status` (read-only)
      report `off` | `git_missing` | `no_repo` | `nested` | `foreign` |
      `ready` | `paused` | `error`, plus message, branch, head,
      enclosing_repo, pending_changes, last_error, can_enable,
      squash_window_minutes and author. `nested`: the project folder's
      parent is inside any git work tree (e.g. a data dir inside the
      ReqManTool checkout); never init, never commit, the user is told to
      move the data dir. `foreign`: `.git` is a gitfile, a symlink or a
      linked worktree. `paused`: another branch, a detached HEAD, a
      merge/cherry-pick/revert/rebase/sequencer in progress or unmerged
      entries; the in-progress check is re-run just before staging.
      (2) Repos: new projects (blank, template after the staging rename,
      subprojects — sibling folders, so no overlap) get `git init` on
      `current` plus an initial commit. Existing projects: "Enable history
      (git)" via `POST /projects/{id}/git/enable` and WebSocket
      `project.git_enable` (broadcasts `changed`, also after a partial
      failure) and the File menu action `project.enableHistory`; 409 /
      -32602 when nested, foreign, detached, `current` exists but is not
      checked out, or an operation is in progress; 400 when git is off or
      missing; on another branch it creates `current` at HEAD.
      (3) Auto-commit: a `_records_history` decorator on every mutating
      `ProjectService` method commits only data files on `current`
      (`project.json`, `modules/*/module.json`,
      `modules/*/requirements/[!.]*.json`, `links/[!.]*.json`); junk
      (`.DS_Store`, `*~`, AppleDouble `._*`) is never committed and the
      store skips dot-files; hand-edited data files are swept into the
      next commit. Generated messages ("SYRS-0012: Status Draft →
      Reviewed; SYRS-0013 added"). Session squash amends HEAD only if it
      has the `ReqMan-Session` trailer, one parent, the same author, is
      within the window and no ref other than `refs/heads/current`
      contains it; a session that cancels out is dropped. Hooks never run
      (`core.hooksPath=/dev/null` on POSIX, a private empty dir on
      Windows); fsmonitor, gc.auto and maintenance off; inherited
      GIT_DIR / GIT_CONFIG_* / pathspec env vars dropped; messages via
      stdin. Files are always saved first; a git failure is shown as
      `last_error` and retried on the next write. (4) Settings are env
      vars, not config keys (no CONFIG_FORMAT bump): `REQMAN_GIT=off`,
      `REQMAN_GIT_SQUASH_MINUTES` (default 10, 0 = never),
      `REQMAN_GIT_AUTHOR_NAME`/`REQMAN_GIT_AUTHOR_EMAIL`,
      `REQMAN_GIT_EXECUTABLE`; the author falls back to the git identity,
      then `<login>@localhost`. (5) History: `GET
      /projects/{p}/modules/{m}/requirements/{r}/history` and WebSocket
      `requirement.history` (read-only): `git log --first-parent` (no
      `--follow`; ids are never reused), per-attribute diffs from parsing
      the JSON at each revision, per-attribute blame; limit 200,
      `truncated` and `uncommitted` flags; `available: false` with a
      reason when there is no repo. Frontend: `RequirementDetailPanel.tsx`
      (Details tab with "last changed by/when" tooltips from the blame,
      History tab with the timeline and diffs), `GitStatusPanel.tsx` on
      the project page (state, warnings, Enable button), API types and
      queries; git status is invalidated after every mutation, history
      refreshed via live sync. `docs/ARCHITECTURE.md` "Project history
      (git)" section. Backend tests 756 → 913 (`test_git_history.py`,
      `test_git_history_edge_cases.py`, `test_git_history_safety.py`,
      `test_git_history_conflicts_and_isolation.py`,
      `test_history_messages.py`, `conftest.py` with git off by default
      and an isolated git config); suite runtime about 27 s → 90 s. Not
      covered (see backlog): baselines, the full project Git panel and
      the 2a follow-ups; the UI was not tried in a browser.
- [x] Git integration, slice 2b: baselines + project Git panel.
      (1) Create: `POST /projects/{id}/baselines` `{name, description}`
      and WebSocket `baseline.create` (broadcasts `changed`, also after a
      part-way failure); allowed only in state `ready` (otherwise 409 /
      -32602; git off/missing → 400). Refusals run first and change
      nothing: name rule (trimmed, 1–100 chars of `A-Za-z0-9._-`, starts
      with a letter/digit, no `..`, not ending `.`/`.lock`, reserved
      `current` and `compare`, verified with `git check-ref-format`),
      duplicate tag → 409, and `baseline` must be an ancestor of
      `current` (`merge-base --is-ancestor`) → otherwise 409. Then
      pending data changes are committed (the baseline covers exactly
      what is on disk; the squash window closes via ref containment),
      the annotated tag `baseline/<name>` is created at HEAD (subject
      `Baseline <name>`, description verbatim, trailers Reqman-Baseline,
      Reqman-Baseline-Date, Reqman-Tool-Version, Reqman-Project-Format;
      tagger = configured author), and `refs/heads/baseline` is
      fast-forwarded with an `update-ref` compare-and-swap — if that
      fails the tag is deleted again. Later failures → `BaselineFailed`
      400 with `last_error` and a `changed` broadcast. (2) List: `GET
      /projects/{id}/baselines` / WS `baseline.list`, newest first,
      parsed from the raw tag objects in one `cat-file --batch`;
      lightweight or foreign `baseline/*` tags are listed with
      `annotated:false` and reduced info (a "tag" badge in the UI).
      (3) Browse read-only: `GET /projects/{id}/baselines/{name}` and
      `.../modules/{m}/requirements` / WS `baseline.get`,
      `baseline.requirements` — the project at the tag's commit, read in
      memory via `ls-tree -r` + `cat-file --batch` filtered to the data
      pathspecs (no checkout), parsed through the format upgrade +
      `Project.from_dict`; a newer-format baseline → 409; broken
      committed module/requirement files are skipped, a broken
      project.json → a clear 400; no mutation accepts a baseline.
      (4) Compare: `GET /projects/{id}/baselines/compare?from=&to=` / WS
      `baseline.compare`, requirement-level semantics via pure
      `build_snapshot`/`compare_snapshots` in `history.py`: per module
      added/removed/modified with per-attribute, parent and order diffs;
      link pair changes per configuration (link-mirror attributes
      excluded from per-requirement diffs); modules added/removed and
      attribute-definition changes at summary level, plus a numeric
      summary; `current` on either side = the working files including
      uncommitted changes. (5) Git log: `GET /projects/{id}/git/log?
      limit=` (default 20, cap 200) / WS `project.git_log`. Frontend:
      `GitStatusPanel.tsx` is now the full project Git panel (state/
      branch/pending badges, warnings, Enable, last 10 commits,
      baselines table with Create/Browse/Compare); new
      `CreateBaselineModal.tsx` (client-side mirror of the name rule,
      inline 400/409 errors), `BaselinePage.tsx` ("Baseline <name>
      (read-only)" module view, no editing), `BaselineComparePage.tsx`
      (from/to pickers URL-synced, summary + per-module change lists);
      routes `/projects/:id/baselines/compare` and
      `/projects/:id/baselines/:name`; API types and queries; baselines/
      git log invalidated after mutations and via live sync. Data format
      unchanged: baselines live only in git refs and tags.
      `docs/ARCHITECTURE.md` updated. Backend tests 913 → 935
      (`test_git_baselines.py`, `test_git_baselines_edge_cases.py`):
      CAS rollback (tag deleted when the branch move fails), failure
      broadcasts over REST and WS, paused/stub/missing-git refusals,
      name limits and unicode rejection, list ordering, broken committed
      files, compare current↔current and module removed, log limit
      clamping, and that a squash never amends a baselined commit. Not
      covered (see backlog): the 2a and review follow-ups; the UI was
      not tried in a browser; no frontend unit tests (no test runner).
- [x] Git remotes: share projects between local tool instances (all four
      parts of the 2026-10-05 item). (1) Configure: a per-project remote
      URL stored as git remote `origin` in the project's own
      `.git/config` (per instance; no project.json or data format change;
      credentials never stored — the user's ssh agent / credential
      helper), set via `PUT /projects/{id}/git/remote` (`url`, or `null`
      to remove) and WS `project.git_set_remote`, validated with `git
      ls-remote`. URL safety: only `ssh://`, `git+ssh`, `git://`,
      `https://` and scp-like `user@host:path`; `<transport>::` helpers
      (`ext::`, `fd::`), a leading `-` and `http://` are rejected;
      `file://` and local paths are rejected unless
      `REQMAN_GIT_ALLOW_FILE_REMOTES=1` (set by the backend tests only);
      git itself is restricted with `GIT_ALLOW_PROTOCOL=ssh:git:https`
      (+`file` with the opt-in) and `protocol.ext/fd.allow=never`
      (superseded 2026-10-07: local-folder remotes allowed, see below). The
      git status gained a `remote` field (url, auto_push, ahead/behind,
      sync_conflict, last_error). (2) Sync: `POST
      /projects/{id}/git/push` / WS `project.git_push` pushes `current`,
      `baseline` and all `baseline/*` tags, never forced; a
      non-fast-forward → 409 "Pull first". `POST /projects/{id}/git/pull`
      / WS `project.git_pull` commits pending changes first, then
      fast-forward only; the local `baseline` is fast-forwarded when
      possible, differing baseline tags are kept with notes. A diverged
      `current` is never merged: 409 and the project is in "sync
      conflict", derived from refs (self-heals once the user resolves it
      in git); while conflicted all mutations and baseline creation are
      blocked with 409. Opt-in auto-push per project (`PUT
      /projects/{id}/git/auto-push` / WS `project.git_set_auto_push`,
      `reqman.autopush` in `.git/config`, cleared when the remote is
      removed or replaced) after each auto-commit and baseline, 10 s
      timeout, failures recorded as `last_error` and never raised;
      auto-pushed commits are never amended, so auto-push effectively
      disables session squashing. (3) Clone: `POST /projects/from-remote`
      / WS `project.create_from_remote` clones `--branch current` into
      staging, validates it like any loaded project (format upgrade,
      newer-format check, slug id) and publishes it atomically under the
      project.json id; id taken → 409, invalid/unreachable → 400, no
      leftovers; `origin` stays configured. (4) Concurrency: instances
      meet at push time (rejection + pull first); live sync stays per
      instance. All five WS methods are mutating and broadcast `changed`
      (a failed diverged pull too); all git calls go through
      `git_repo.py`. Backend: `git_repo.py`, `service.py`, `app.py`.
      Frontend: `GitStatusPanel.tsx` "Remote" section (set/change/remove
      URL, Push/Pull, ahead/behind, auto-push switch, sync-conflict
      alert, last error); new `AddProjectFromRemoteModal.tsx` (also a
      button in the home-page empty state); File menu actions
      `project.gitPush`, `project.gitPull` and `file.addProjectFromRemote`;
      API types and queries (pull invalidates the whole project). Backend
      tests 935 → 997 (`test_git_remotes.py`,
      `test_git_remotes_edge_cases.py`, `test_git_remote_security.py`;
      `conftest.py` sets the file-remote opt-in for local bare repos).
      Not covered: real credential-requiring remotes (the tests are
      offline by design); the review follow-ups (see backlog); the UI was
      not tried in a browser; no frontend unit tests (no test runner).
- [x] Left panel tabs: Workspace / Project / Module (2026-10-04 item). The
      left panel (`Sidebar.tsx`) has three drill-down tabs; the active tab
      (`reqman.leftPanel.tab`) and the last chosen project
      (`reqman.leftPanel.projectId`) are remembered per browser.
      **Workspace**: the project tree (projects, subprojects, stub
      projects, loading/error/empty states, "New project"), no longer
      listing modules; a separate chevron expands a project in place,
      clicking a project opens it and switches to the Project tab.
      **Project** (new `ProjectTab.tsx`): the project from the URL or the
      last pick, "Up to <parent>", subprojects (drill down), "New
      subproject", modules with their prefix and "New module"; clicking a
      module opens it and switches to the Module tab; loading, error,
      empty, not-found and stub states. **Module** (new
      `ModuleOutline.tsx` + pure helpers in `requirementOutline.ts`): a
      collapsible outline of the open module — each node shows id, start
      of ObjectText and an Object Type icon (attribute id `object_type`
      or name "Object Type"); expand/collapse all; a filter on id/text
      showing matches with their ancestors expanded; clicking a node
      selects it and scrolls the table via `?highlight=`, a selection
      made elsewhere opens and scrolls the outline; right-click opens the
      existing requirement context menu; keyboard Up/Down, Enter/Space,
      Left/Right; drag-and-drop reorders or reparents within the module
      (before, after, inside, first child of an expanded node, end of the
      top level), one server call per drop. Server: move takes an
      optional `index` (`PUT /projects/{p}/modules/{m}/requirements/{r}/
      parent` body / WS `requirement.move`, via
      `ProjectService.move_requirement`) — the 0-based position among the
      new parent's children, clamped past the end, invalid → 400 /
      -32602; without it the move appends as before; with it the new
      parent's children are renumbered 0..n-1 (repairing duplicate legacy
      orders); one commit and one broadcast per move. Module page: the
      scroll to a revealed row waits until the row has rendered.
      `docs/ARCHITECTURE.md` documents `index`. Backend tests 997 → 1025
      (`test_service.py`, `test_api.py`, `test_git_history.py`). Browser
      smoke test against a scratch backend passed (drill-down, filter,
      drag-and-drop, context menu, live sync, reload). Not covered: the
      outline selection is not in the URL (only tab and last project, per
      browser); project references in Workspace, module folders, the
      collapsed rail and context menus on project/module nodes wait for
      those items; no frontend unit tests (no test runner; `planDrop`
      checked with a throwaway script); review follow-ups (see backlog).
- [x] Toolbar buttons → menu items: Import, Export, Purge (2026-10-06
      item). Frontend only, no backend change. The module page toolbar
      loses the Import CSV, Export ReqIF/CSV/XLSX/DOCX and Purge Module
      buttons and keeps Add Requirement, Paste at root and the view
      selector (the project page had no such buttons). The menu bar uses
      shared registry actions: File → Import ▸ CSV… (`file.importCsv`,
      module open); File → Export ▸ ReqIF / CSV / Excel (XLSX) / Word
      (DOCX) (`file.export.<format>`), enabled whenever a project is
      loaded, the URL built from the project id; Edit → a divider, then
      the red "Purge module…" (`module.purge`, module open) and "Purge
      project…" (`project.purge`, project open), both in the new
      `frontend/src/actions/purgeActions.tsx`. Purge reuses the existing
      confirm modal (there was no typed-name confirm); ids are captured
      when the dialog opens, so a route change cannot redirect the purge;
      no navigation after a purge. New `AppCommands.purgeModule` /
      `purgeProject`; `usePurgeModule` takes the ids as mutation
      variables; new `usePurgeProject` calls the existing `POST
      /projects/{id}/purge` and invalidates every module query of the
      project (live sync covers the rest). `docs/ARCHITECTURE.md`
      (actions) and README (menu bar) updated. Backend tests unchanged at
      1025. Not covered: no frontend tests (no test runner); not tried in
      a browser; the later left/right-click menus do not use these
      actions yet; review follow-ups (see backlog).
- [x] Fixed layout: sticky table header, independently scrolling panels
      (2026-10-05 item). Frontend only, no backend change. The app shell
      stays fixed: in `layout/AppLayout.tsx` the AppShell padding is 0,
      the main area is exactly one viewport high (`.reqman-main`), and
      the page content scrolls in `.reqman-page`, which now carries the
      padding. Module page: the requirements table sits in its own scroll
      container (`.reqman-table-scroll`, one native `overflow: auto` for
      both axes) with Mantine `stickyHeader`; the header stays visible
      and opaque in light and dark mode; rows revealed by `?highlight=`
      or by the outline clear the header via `scroll-padding-top: 3rem`.
      The table/detail row fills the page (`.reqman-fill-page`,
      min-height 26rem; below that the page area scrolls).
      `RequirementDetailPanel.tsx` keeps its title and tab list fixed
      while the Details and History panels scroll on their own. Baseline
      page: the same sticky-table pattern; the card fits the viewport
      (`.reqman-card-table` matches the header background to the card).
      The left panel already scrolled independently (unchanged); compare,
      home and project pages scroll inside the page area. Files:
      `frontend/src/index.css`, `layout/AppLayout.tsx`,
      `pages/ModulePage.tsx`, `pages/BaselinePage.tsx`,
      `components/RequirementDetailPanel.tsx`. Backend tests unchanged at
      1025. Browser smoke test (Chromium) against a scratch backend with
      75 requirements passed in light and dark mode, at a small window
      and on the baseline page. Not covered: no frontend tests (no test
      runner); Safari and Firefox not tried; review follow-ups (see
      backlog).
- [x] Resizable table columns (2026-10-04 item). The requirements table
      (`ModulePage.tsx`) and the baseline table use `table-layout: fixed`
      with a `<colgroup>`; the sticky header and horizontal scroll keep
      working. Drag a header's right edge to resize (pointer capture, live
      preview); double-click the edge to auto-fit (capped at 800px); the
      edge is a focusable `role="separator"` (Arrow ±10px, Shift+Arrow
      ±50px, Enter auto-fits). Widths are bounded to 40–4000px; the
      Actions column takes the leftover space. Wrap per column: wrap is
      the default and now shows the full text instead of the old 2-line
      clamp; single-line mode uses an ellipsis plus a tooltip. Column
      header menu (⋮ button or right-click on the header): Wrap text,
      Auto-fit width, Reset column widths, Save column widths to view,
      Remove column widths from view; View menu registry actions
      `view.resetColumnWidths`, `view.saveColumnWidths`,
      `view.removeViewColumnWidths`. Per browser, localStorage
      `reqman.columns:module/<project>/<module>/<viewId|default>` holds
      `{widths, singleLine}`; width precedence is browser, then view, then
      the attribute-type default. The baseline table shares the module's
      "All attributes" layout. Shared view widths: new optional
      `View.column_widths` in `project.json`, written only when set, so
      existing files stay byte-identical (no PROJECT_FORMAT bump; older
      readers ignore the field but drop it on their next write).
      `ProjectService.create_view(column_widths=)` and
      `set_view_column_widths` (keys `"@id"` or the view's own attribute
      ids, integer values 40..4000, null or `{}` clears); REST `PUT
      /projects/{p}/views/{v}/column-widths`, `POST /projects/{p}/views`
      accepts `column_widths`; WebSocket `view.set_column_widths`,
      `view.create` accepts `column_widths`; 400/-32602 for invalid input,
      404/-32001 for a missing view; changes broadcast. Files:
      `backend/reqman/{models.py, service.py, app.py}`,
      `backend/tests/test_view_column_widths.py` (new, 19 tests),
      `frontend/src/hooks/columnLayout.ts` (new),
      `components/ResizableHeaderCell.tsx` (new),
      `pages/{ModulePage.tsx, BaselinePage.tsx}`,
      `components/EditableAttributeCell.tsx` (`wrap` prop),
      `actions/{appActions.ts, registry.ts, types.ts,
      AppActionsProvider.tsx}`, `api/{types.ts, queries.ts}`,
      `index.css`. Backend tests 1025 → 1044. Browser smoke test
      (Chromium) passed for drag, minimum width, auto-fit, keyboard, wrap
      toggle, reset, save to view / fresh session / remove with live
      sync, and the baseline page. Not covered: no report grids exist
      yet; the detail panel has no label column (labels sit above the
      values), so it is unchanged (left to "Collapsible (and resizable)
      side panels"); no frontend tests (no test runner); touch input and
      dark mode not tried; review follow-ups (see backlog).
- [x] **Local-folder / network-share remotes** (2026-10-07 decision,
      follow-up of Git remotes). Absolute local paths and `file:///abs`
      URLs (e.g. a NAS mount) are accepted as project remotes in normal
      operation; the `REQMAN_GIT_ALLOW_FILE_REMOTES` test opt-in is
      removed. Still rejected: `<scheme>::` helpers (`ext::`, `fd::`, also
      with a leading digit like `1abc::`), a leading `-`, relative paths,
      `file://host/...`, `http://` and other schemes, and non-lower-case
      schemes (git matches them case-sensitively). Local remotes inside or
      equal to the data directory are refused for both set-remote and
      clone (realpath/samefile; symlinks and percent-encoding handled), so
      one project cannot push into another. `GIT_ALLOW_PROTOCOL` is
      `ssh:git:https` for every git call; `:file` is added only for the
      remote operations (ls-remote, push, fetch, clone). Git's "transport
      'x' not allowed" maps to a clear 400 message. Security for pushes to
      a local remote: receive-pack runs with `core.hooksPath` pointed at
      an empty dir, plus `core.alternateRefsCommand=true`,
      `core.fsmonitor=false`, `receive.denyCurrentBranch=refuse`,
      `receive.autogc=false`, `gc.auto=0`, `maintenance.auto=false`, so
      the target repo's hooks and config cannot run programs on this
      machine; mixed local/network push URLs are refused; a push to a
      checked-out branch is rejected (400). The Git panel and "Add project
      from remote" hints mention local folders / network shares;
      `docs/ARCHITECTURE.md` gained a "Remote URLs" bullet. Files:
      `backend/reqman/{git_repo.py, service.py}`,
      `backend/tests/{conftest.py, test_git_remote_security.py}`,
      `docs/ARCHITECTURE.md`,
      `frontend/src/components/{GitStatusPanel.tsx,
      AddProjectFromRemoteModal.tsx}`. Backend tests 1044 → 1096. Fix
      rounds: 2 (receive-side hooks, then target-config command vectors).
      Not covered: Windows drive/UNC paths were tested only via a flag;
      not tried in a browser.
- [x] **Per-module attribute applicability** (2026-10-04 item). A project
      attribute can be limited to selected modules:
      `AttributeDefinition.modules` holds module ids and is written only
      when non-empty (absent / empty = all modules). PROJECT_FORMAT 1 → 2
      with a tested, idempotent v1→v2 migration. Rules in
      `ProjectService`: writing a value for a non-applicable attribute is
      rejected (400 / -32602) on add, update and CSV import; `required`
      and defaults apply only in the listed modules. CSV import: a
      non-empty cell for a non-applicable attribute rejects the import
      with a row error; a column that is empty throughout goes to
      `ignored_columns`. CSV/XLSX/DOCX/ReqIF exports show only the
      applicable attributes per module. New REST `PUT
      /projects/{id}/attributes/{attr_id}/modules` and WebSocket
      `attribute.set_modules`: narrowing the scope while non-blank values
      exist outside it is refused unless `drop_values` is true, which
      removes them and reports them in `dropped_values` (blank values are
      removed silently); the change broadcasts. Templates declare
      `modules` by module key; `rbc_template.json` scopes the test fields
      (preconditions, test_steps, expected_result, test_result) to
      SysTest/SwTest and SIL, Allocation and Verification Method to
      StRS/SyRS/SwRS. User rule (2026-10-07): only optional attributes
      can be scoped; required attributes always apply to every module.
      Add / set-modules with a required attribute and a non-empty list
      → 400 / -32602 "Required attribute '<Name>' applies to every
      module; only optional attributes can be limited to modules";
      templates with a required scoped attribute are rejected;
      hand-edited required+scoped attributes load and are treated as
      applying everywhere. UI: module checklist in the Add attribute
      dialog (disabled while Required is ticked); Modules column and
      Modules editor dialog on the project page (the Modules button is
      disabled with a tooltip for required attributes); tables, views,
      the detail panel and forms show only the module's applicable
      attributes. Files: `backend/reqman/{models.py, service.py, app.py,
      csv_import.py, formats.py, templates.py,
      templates/rbc_template.json}`,
      `backend/tests/{test_attribute_applicability.py (new),
      test_required_attribute_scope.py (new, 9 tests),
      test_git_baselines.py, test_templates_edge_cases.py}`,
      `docs/ARCHITECTURE.md`, `frontend/src/api/{applicability.ts,
      queries.ts, types.ts}`, `components/{AddAttributeModal.tsx,
      AttributeModulesChecklist.tsx, AttributeModulesModal.tsx}`,
      `pages/{ProjectPage.tsx, ModulePage.tsx, BaselinePage.tsx}`.
      Backend tests 1096 → 1120. Not covered: no frontend tests (no test
      runner); not tried in a browser; review follow-ups (see backlog).
- [x] **Row height resizing (Excel-like)** (2026-10-07 user addition to
      "Resizable table columns"). Frontend only; row heights are kept per
      browser only. In the requirements table and the baseline table, a
      row's bottom border shows a `row-resize` cursor: dragging it resizes
      the row live (pointer capture); double-clicking it auto-fits the
      row to its content and clears the fixed height. The minimum height
      is the cell padding plus 28px, set once in the
      `--reqman-row-min-line` CSS variable. Content clipped by more than
      one line gets a fade on wrap columns; single-line columns keep
      their ellipsis; an open inline editor lifts the clipping. The Id
      cell's handle is keyboard-accessible (ArrowUp/Down ±5px, Shift
      ±25px, Enter fits; Alt, Ctrl and Meta are left for the shortcuts).
      A plain click on the handle still selects the row, and a cell
      double-click still opens the editor. Heights are stored with the
      column widths in localStorage
      `reqman.columns:module/<p>/<m>/<view>` as `rowHeights`; old entries
      still parse. "Reset row heights" is in the View menu (registry
      action `view.resetRowHeights`) and in the column header menu. Also
      fixed follow-ups (1)–(3) of "Resizable columns: review follow-ups
      (2026-10-07)": the sticky `th` override is removed; an unmount
      mid-drag no longer leaves the body cursor and user-select stuck;
      the column handle moved inside its cell (no more 4px overflow).
      Files: `frontend/src/hooks/columnLayout.ts`,
      `components/{RowResizeHandle.tsx (new),
      ResizableHeaderCell.tsx}`, `index.css`,
      `pages/{ModulePage.tsx, BaselinePage.tsx}`,
      `actions/{appActions.ts, registry.ts}`. Backend unchanged at 1120.
      Headless Chrome smoke test passed. Review: 1 fix round. Not
      covered: no frontend tests; Safari/Firefox not tried; review
      follow-ups (see backlog).
- [x] **Row styling by Object Type (and other enum values)** (requested
      2026-10-07). Per-project rules in `project.json` `row_styles` (written
      last, only when non-empty): `{attribute_id, value, font_color,
      background_color, bold}`. PROJECT_FORMAT 2 → 3; the migration drops
      any stray key, so existing projects start with no rules.
      `ProjectService.set_row_styles` validates: known non-system enum
      attribute, valid choice, `#rrggbb` or null colors (stored
      lower-case), bool bold, no duplicates, at least one effect per rule,
      at most 200 rules, no unknown keys. The first matching rule wins;
      stale hand-edited rules are ignored everywhere. REST `PUT
      /projects/{id}/row-styles`, WS `project.set_row_styles`, both
      broadcast; history message "Row styles changed". Templates may
      declare `row_styles`; RBC template defaults Heading `#2f9e44` font,
      Information `#1971c2` font; save-as-template keeps the rules that
      still resolve; blank projects get none. Applied in the requirements
      table, the Module outline, the baseline view (from the snapshot's
      own rules), baseline compare (per-module `row_styles`: the "to" side
      for added/modified, the "from" side for removed) and the DOCX export
      (font color, `w:shd` shading, bold). Dark mode adjusts colors for
      display only; DOCX and storage keep them as given. UI:
      `RowStylesModal` (attribute/value selects, ColorInput pickers, bold,
      reorder, live light/dark preview, stale-rule warnings), opened from
      View ▸ Row styles (registry action `view.rowStyles`) and a button on
      the project page. `docs/ARCHITECTURE.md`: format 3, "Row styles"
      section, routes. Files: `backend/reqman/{models.py, formats.py,
      service.py, app.py, docx_export.py, history.py, templates.py,
      templates/rbc_template.json}`, `backend/tests/{test_row_styles.py
      (new, 24 tests), test_row_styles_edge_cases.py (new, 39 tests),
      test_attribute_applicability.py, test_templates.py,
      test_templates_edge_cases.py}`, `frontend/src/components/
      {RowStylesModal.tsx (new), rowStyles.ts (new), ModuleOutline.tsx}`,
      `pages/{ModulePage.tsx, BaselinePage.tsx, BaselineComparePage.tsx,
      ProjectPage.tsx}`, `actions/{types.ts, appActions.ts, registry.ts}`,
      `layout/AppDialogs.tsx`, `api/{types.ts, queries.ts}`, `index.css`.
      Backend tests 1120 → 1183. Not covered: no frontend tests; not
      tried in a browser; no styling in CSV/XLSX/ReqIF (not requested);
      review follow-ups (see backlog).
- [x] **Rich text in requirement text, slice 1: storage, sanitiser,
      editor** (requested 2026-10-07; exports are slice 2, see backlog).
      Storage and detection: `text` attributes (incl. ObjectText) may hold
      a sanitised XHTML subset. A value is rich only if it starts with the
      exact ReqIF wrapper `<div xmlns="http://www.w3.org/1999/xhtml">`;
      anything else is plain. Unformatted rich text is stored as plain
      text, so plain values stay plain. `from_plain` keeps plain text that
      literally starts with the wrapper. Sanitiser: new
      `backend/reqman/richtext.py`, Python stdlib only. Strip-only
      allow-list: p, br, strong/b, em/i, u, s/strike/del, ul, ol, li, and
      span with `color` (`#rrggbb`; `#rgb`/`rgb()` normalised) and
      `font-size` 10/12/14/16/20/24px. script/style/iframe/svg/math are
      dropped with their content; other tags are unwrapped; all other
      attributes are dropped. HTML implied end tags (`p`/`li`) and
      reopening of formatting elements, so it matches the browser.
      Deterministic and idempotent. Bounded: 2,000,000 characters and
      300,000 nodes on input and result, a clone budget, depth caps, O(1)
      scope checks. Over-limit values are rejected as 400/-32602 "Rich
      text is too large". Lenient reads never raise. A parser failure
      falls back to literal text. Plain-text projection `to_plain`, used
      for search, the outline, history messages, the required/blank check
      and exports. `ProjectService` sanitises on add, update and copy, and
      for text defaults (including at merge time; stored over-limit or
      invalid defaults are skipped). CSV import keeps cells literal
      (`from_plain`). PROJECT_FORMAT 3 → 4 (no-op migration); a rich
      update also rewrites `project.json` to v4 in the same commit. For
      now every export (CSV/XLSX/DOCX/ReqIF) writes the plain projection.
      Frontend: TipTap editor via @mantine/tiptap 9.6.1 + @tiptap/* 3.31.4
      (new dependencies), lazy-loaded in its own chunk. Toolbar: bold,
      italic, underline, strikethrough, bullet/numbered lists, colour,
      font size. Used in the inline cell editor, detail panel and add
      form. Esc cancels, Ctrl/Cmd+Enter saves, outside click saves,
      whitespace preserved. `RichTextView` renders through a client mirror
      sanitiser (`richText.ts`, DOMParser-based) in table cells, detail
      panel, baseline view; plain projection in the outline, compare and
      history. Files: `backend/reqman/{richtext.py (new), service.py,
      store.py, formats.py, history.py}`, `backend/tests/{test_rich_text.py
      (new), test_rich_text_edge_cases.py (new), test_rich_text_limits.py
      (new), test_row_styles.py}`, `frontend/src/components/
      {RichTextField.tsx (new), LazyRichTextField.tsx (new),
      RichTextView.tsx (new), richText.ts (new), EditableAttributeCell.tsx,
      RequirementFormModal.tsx, RequirementDetailPanel.tsx,
      requirementOutline.ts}`, `pages/{BaselinePage.tsx,
      BaselineComparePage.tsx, ProjectPage.tsx}`, `index.css`,
      `package.json`, `package-lock.json`. Backend tests 1183 → 1308.
      Review: 3 fix rounds (implied end tags / format-version write /
      editor whitespace; a DoS blowup in formatting reopen; output-size
      limits). Not covered: no frontend tests (the client sanitiser
      mirror is untested); smoke-tested headless only; review follow-ups
      (see backlog).
- [x] **Rich text, slice 2: exports** (2026-10-08; requested 2026-10-07).
      DOCX (Word export): rich `text` values keep formatting —
      bold/italic/underline/strike runs, span colour and size (px × 0.75
      pt), `br` line breaks. The first rich paragraph shares the bold
      label paragraph. Lists use the Word `List Bullet`/`List Number`
      styles (levels 2/3 use the `2`/`3` styles; deeper levels reuse
      level 3 with 0.25" extra indent per level); further paragraphs in a
      list item use `List Continue N`; every `ol` restarts at 1 (own
      numbering instance). A row-style font colour does not override a
      span colour. Plain values are filtered through `richtext.xml_text`,
      fixing a pre-existing bug where a control character in any value
      made the DOCX export fail ("All strings must be XML compatible").
      ReqIF: every `text` attribute (ObjectText included) is exported as
      ATTRIBUTE-DEFINITION-XHTML / DATATYPE-DEFINITION-XHTML (the
      datatype is declared only if the project has a text attribute);
      typing is per attribute because plain and rich values coexist. Rich
      values are written from the canonical tree as `<xhtml:div>`; plain
      values are escaped, with `xhtml:br` for newlines and `&#160;` for
      spacing; `u`/`s` become `xhtml:span` with text-decoration (ReqIF's
      XHTML subset lacks u/s); string/enum values are filtered for
      XML-invalid characters. Output re-parses with the `reqif` library.
      CSV/XLSX unchanged (plain projection). Endpoints unchanged (existing
      DOCX/ReqIF export routes). `richtext.py`: new `blocks()` (returns []
      for non-rich/non-string/blank input, never raises), `span_style()`,
      `xml_text()`, public tree aliases; surrogates added to the invalid
      characters. `service.py`: `_export_modules(project,
      keep_rich=False)`; DOCX/ReqIF pass `keep_rich=True`. UI: existing
      Word/ReqIF export actions. Files: `backend/reqman/{docx_export.py,
      reqif_export.py, richtext.py, service.py}`, `docs/ARCHITECTURE.md`.
      Not covered: tests pending (see backlog follow-ups); no ReqIF
      importer for XHTML; tables/images in a later slice.
- [x] **Left-click and right-click menus everywhere** (2026-10-08;
      requested 2026-10-04). Frontend only; no backend, API or data
      format change. Shared action registry in `frontend/src/actions/`
      with element types app, requirement, project, module, column and
      link; menu layouts `REQUIREMENT_CONTEXT_MENU` /
      `REQUIREMENT_NAVIGATION_MENU`, `LINK_CONTEXT_MENU` /
      `LINK_NAVIGATION_MENU`, `COLUMN_HEADER_VIEW_MENU` /
      `COLUMN_HEADER_EDIT_MENU`, `PROJECT_NODE_MENU`, `MODULE_NODE_MENU`
      and an extended Edit menu. `isSupported` hides entries,
      `isAvailable` disables them. One app-wide `ContextMenuHost` (Esc,
      outside click or item click closes; focus returns). Requirement
      rows, cells and outline, right click: Cut, Copy, Paste as child,
      Paste as sibling, Paste at root, Add child, Add sibling, Move
      Up/Down, Move to parent, Link to, Delete. Left click on the Id cell
      opens a navigation menu: Show details, Show in tree, Go to linked
      requirement ▸; elsewhere left click still selects and double-click
      still edits. The page-local Add child / Move / Link dialogs became
      registry actions (`AppDialogs`). Link badges: left click Go to;
      right click Go to + Remove link… (confirm; existing links DELETE
      route). Sidebar / Project tab, right click: project, subproject and
      stub — Open, New subproject, New module, Save as template; module —
      Open, Move Up/Down (`module.reorder`), New module. Column headers:
      left click opens the view menu (Wrap text, Auto-fit, width and
      row-height entries); right click is a superset adding "Attribute
      modules…" (hidden for Id/link/object_text, disabled for required).
      Keyboard: Delete and Cmd/Ctrl+Backspace delete (with confirm); F10
      or an Alt tap focuses the menu bar; Shift+F10 / menu key opens the
      editing menu of the focused element or selected row; Help ▸
      Keyboard shortcuts updated. Decisions: left-click menus only on the
      Id cell and link badges; `project.newSubproject` and
      `file.saveAsTemplate` became project actions. Endpoints unchanged
      (existing requirement, link and module routes). Files:
      `frontend/src/actions/{ContextMenuHost.tsx (new), columnActions.ts
      (new), linkActions.tsx (new), links.ts (new), projectActions.ts
      (new), ActionMenuEntries.tsx, AppActionsProvider.tsx,
      appActions.ts, context.ts, registry.ts, requirementActions.tsx,
      shortcuts.ts, types.ts}`, `frontend/src/layout/{leftPanel.ts (new),
      AppDialogs.tsx, MenuBar.tsx}`, `frontend/src/components/
      {EditableAttributeCell.tsx, KeyboardShortcutsModal.tsx,
      ModuleOutline.tsx, ProjectTab.tsx, RequirementFormModal.tsx,
      ResizableHeaderCell.tsx, Sidebar.tsx}`, `frontend/src/pages/
      {ModulePage.tsx, ProjectPage.tsx}`, `frontend/src/api/queries.ts`.
      Not covered: no frontend tests (no runner), no browser verification
      yet; follow-ups in the backlog ("Context menus: follow-ups").
- [x] **Menu bar product decisions + template list loading** (2026-10-08;
      follow-up of the menu bar item, 2026-10-04). (1) Ctrl/Cmd+V with
      the copied requirement itself selected pastes a copy as a sibling
      right after it (copy + move with index through the existing server
      operations); "Add sibling" works the same way. Two server calls and
      two commits, not atomic — a warning is shown if the move fails.
      (2) Export ▸ on the project page was already done 2026-10-06 with
      the toolbar → menu item. (3) The templates list is loaded lazily,
      when the template submenu opens or `NewProjectModal` mounts
      (`useTemplates(enabled)` in `frontend/src/api/queries.ts`), instead
      of on every app load. Frontend only; endpoints unchanged. UI: Edit
      menu / requirement context menu, File ▸ Load template ▸, New
      project dialog.
- [x] **Collapsible and resizable side panels** (2026-10-08; requested
      2026-10-04). Frontend only; no backend, API or data format change.
      The left navigation panel and the right requirement-detail panel
      each collapse to a 28px rail (chevron on the panel edge; a click on
      the rail expands it). View menu: "Navigation panel"
      (`view.toggleLeftPanel`, Ctrl/Cmd+B) and the detail panel
      (`view.toggleDetailPanel`, now Ctrl/Cmd+Alt+B); shortcuts are
      inactive while typing. Draggable border (`role="separator"`; arrow
      keys resize, Shift for bigger steps, double-click resets). Widths:
      left 200–640px (default 280, max 40% of the window), right
      260–760px (default 320, max 45%); the window fraction is applied at
      render only. State and widths are kept in localStorage
      (`reqman.leftPanel.open`, `reqman.leftPanel.width`,
      `reqman.detailPanel.width`, existing `reqman.detailPanelOpen`), not
      in git-tracked data. Narrow screens (max-width 48em) start
      collapsed and open one overlay at a time (backdrop click or Escape
      closes; a route change closes). The table takes the freed width;
      "Show in tree" opens the left panel. Endpoints: none. Files:
      `frontend/src/layout/{sidePanels.ts (new), SidePanelParts.tsx
      (new), SidePanelLayout.tsx (new), AppLayout.tsx}`,
      `frontend/src/actions/{AppActionsProvider.tsx, appActions.ts,
      registry.ts, types.ts}`, `frontend/src/pages/ModulePage.tsx`,
      `frontend/src/components/RequirementDetailPanel.tsx`,
      `frontend/src/index.css`, `README.md`. Checked with static headless
      screenshots at 1400px and 600px. Not covered: interactive browser
      verification, overlay focus handling, frontend tests; follow-ups in
      the backlog ("Side panels: follow-ups").
- [x] **Multiple open modules (tabs), detachable module windows, and
      drag-and-drop linking** (2026-10-08; requested 2026-10-04).
      Tabs: opening a module in a main window adds a tab "project ·
      module"; click switches, middle-click / × / Delete closes, arrow
      keys move focus, drag reorders; right-click `MODULE_TAB_MENU`
      (Close tab, Close other tabs, Close tabs to the right, Move
      left/right, Detach into new window). Closing the active tab opens
      the right neighbour, else the left, else the project page. The tab
      list is kept in localStorage (`reqman.moduleTabs`, keyed by data
      folder) and shared by all main windows via the storage event; each
      window has its own active tab (its route). Tabs whose module fails
      to load show a warning; the bar is hidden with no tabs. View menu:
      Close tab, Detach tab; sidebar module menu: Open in new tab (no
      switch), Open in new window. Detached windows: route
      `/detached/projects/:projectId/modules/:moduleId`, minimal chrome
      (title, Re-attach, dark mode) with its own live sync, shortcuts,
      menus and dialogs; one named window per module; a blocked popup
      shows a notification and keeps the tab; Detach removes the tab;
      Re-attach posts an origin-checked message to the opener, which
      reopens the tab. Requirement navigation menu: Open in new window.
      Drag-and-drop linking: the Id cell is the drag handle, payload
      `application/x-reqman-requirements+json` {kind, version: 1,
      projectId, moduleId, requirementIds}; rows highlight as drop
      targets, hovering a tab for 600 ms switches to it, cross-window via
      native DataTransfer. On drop the server returns the options: none →
      warning, no link; all existing → "already linked"; one → created in
      the configured direction; several → picker dialog. Same-module
      drops only with an intra-module configuration; cross-project drops
      and more than one id are rejected with a warning for now.
      Endpoints: `GET /projects/{project_id}/links/options?from_id=&to_id=`
      and WS `requirement.link_options` (read-only, no broadcast);
      backend `ProjectService.link_options` in `backend/reqman/service.py`
      (configurations in project order, oriented for either drag
      direction, `exists` flag; same id → 400, unknown → 404). Files:
      `backend/reqman/{service.py, app.py}`, `frontend/src/{App.tsx,
      api/types.ts, api/queries.ts, index.css}`,
      `frontend/src/actions/{types.ts, AppActionsProvider.tsx,
      registry.ts, projectActions.ts, tabActions.ts (new)}`,
      `frontend/src/layout/{moduleTabs.ts (new), ModuleTabBar.tsx (new),
      DetachedLayout.tsx (new), AppLayout.tsx, AppDialogs.tsx}`,
      `frontend/src/components/{requirementDrag.ts (new),
      DropLinkModal.tsx (new)}`, `frontend/src/hooks/useDropLink.ts`
      (new), `frontend/src/pages/ModulePage.tsx`. Not covered: split
      view, multi-selection drag, browser verification, backend tests
      for `link_options`; follow-ups in the backlog ("Module tabs:
      follow-ups").
- [x] **Module types + traceability matrix, verification report and test
      execution report** (2026-10-08; requested 2026-10-04, delivered in
      three slices). Slice 1, module types (commit c9d5c86):
      `Module.type` ∈ `requirements` | `test_cases` | `test_execution`,
      written only when not the default, chosen at creation (Type select
      in the New module dialog) and immutable; icon in the sidebar
      Project tab, module tabs and project page. `PROJECT_FORMAT` 4 → 5
      (the migration rewrites nothing). `AttributeDefinition.module_types`:
      an attribute applies to the listed modules or to modules of the
      listed types; required attributes cannot be type-scoped. New link
      configurations are rejected (not warned) unless requirements →
      requirements, test_cases → requirements or test_execution →
      test_cases; stored ones are never re-checked. An unknown module type
      in a template makes the template invalid. `RBC_Template`:
      SysTest/SwTest = test_cases; new SysTestExec (SYSTX) / SwTestExec
      (SWTX) = test_execution with "Executes SysTest" / "Executes SwTest",
      attributes Result (Passed/Failed/Blocked/Not Run), Executed By,
      Execution Date, Build/SW Version Under Test, Evidence/Log Ref, a
      "Test Execution" view and row styles; the old "Test Result" on test
      cases is kept next to the execution "Result" (duplication, see
      follow-ups). Endpoints: `POST /projects/{project_id}/modules`
      (`type`), `POST /projects/{project_id}/attributes` (`module_types`),
      `PUT /projects/{project_id}/attributes/{attr_id}/modules`
      (`{modules, module_types, drop_values}`); WS `module.create`,
      `attribute.add`, `attribute.set_modules`. Slice 2, reports (commit
      3f9b518), read-only in `ProjectService` with REST + WS parity and no
      broadcast: traceability matrix over a chain of link configurations
      (grid, coverage "N of M", orphans, unverified; CSV/XLSX export;
      `GET /projects/{project_id}/reports/traceability?config=…[&config=…]`,
      `.../traceability/export/{csv|xlsx}`, WS `report.traceability`);
      verification report per requirements module (verified / failed /
      not run / not covered, totals and %; DOCX/XLSX;
      `GET /projects/{project_id}/modules/{module_id}/reports/verification`
      `[/export/{docx|xlsx}]`, WS `report.verification`); test execution
      report per test_execution module (executed vs. planned, counts,
      failed tests with affected requirements, builds/executors/dates;
      DOCX/XLSX; `.../reports/execution[/export/{docx|xlsx}]`, WS
      `report.execution`). Attributes are found by role (name priority
      lists), not by RBC ids; the latest execution is the greatest
      Execution Date, then module order, then tree position;
      Heading/Information object types are excluded. UI: Reports menu,
      `TraceabilityPage`, `VerificationReportPage`, `ExecutionReportPage`.
      Slice 3, result import and verification status: JUnit XML and Robot
      Framework `output.xml` into a test_execution module (stdlib expat,
      DOCTYPE/entities refused, 64 MiB / 10,000 tests limits); matched on
      a test-id-role attribute or the requirement id, exact then
      whole-word; read-only preview, then an all-or-nothing import (one
      commit, one broadcast); unmatched/ambiguous results are skipped with
      a warning; JUnit error/failure → Failed, skipped → Not Run.
      Endpoints: `POST /projects/{project_id}/modules/{module_id}/import/`
      `test-results/preview` and `.../import/test-results` (raw XML body
      plus query options, like CSV import; no multipart), WS
      `requirement.preview_test_results` /
      `requirement.import_test_results`. Computed read-only Verification
      status (`GET /projects/{project_id}/modules/{module_id}/`
      `verification-status`, WS `report.verification_status`): optional
      per-browser table column (View menu toggle) and detail panel
      display; never stored, not exported. UI: `ImportTestResultsModal`
      from File ▸ Import and the module menu "Import test results…".
      Backend: `backend/reqman/{models.py, service.py, app.py,
      formats.py, templates.py, templates/rbc_template.json,
      report_export.py (new), test_results_import.py (new)}`; design in
      `docs/ARCHITECTURE.md` ("Module types and date attributes",
      "Reports", "Test result import", "Verification status"). Not
      covered: tests for all three slices; follow-ups in the backlog
      ("Module types and reports: follow-ups").
- [x] **`date` attribute type** (2026-10-08; delivered with slice 1 of the
      module types item, commit c9d5c86). ISO `YYYY-MM-DD` string; a real
      calendar date is validated in `ProjectService`, defaults included.
      Native date picker in the table and the requirement form; CSV
      import trims and validates; XLSX export writes real date cells;
      ReqIF export uses `DATATYPE-DEFINITION-DATE` as
      `YYYY-MM-DDT00:00:00` and leaves invalid stored values out.
      Endpoints: existing attribute and requirement routes (`type:
      "date"`). Backend: `backend/reqman/{service.py, csv_import.py,
      xlsx_export.py, reqif_export.py}`; UI: Add attribute dialog,
      `EditableAttributeCell`, `RequirementFormModal`. The `user` type is
      not delivered (remaining item in the backlog); "Attribute schema
      evolution + multi-valued enum" stays as is.
- [x] **Project references + cross-project traceability** (2026-10-08;
      requested 2026-10-04). `Project.references`: explicit project ids,
      the parent project is referenced implicitly; dangling id → 404,
      self → 400, unloadable → 409/400, duplicates collapse, at most 100.
      Cross-project link configurations:
      `LinkConfiguration.target_project_id` must be a referenced project;
      module type pairs still apply; the configuration, link pairs and
      mirror attribute stay in the source project; mirror values are
      project-qualified by project id (`> rbc-gp:SYRS-0001`); link/unlink
      accept plain or qualified target ids. The target side is computed
      on read (mirrors are not written into the target project) and shown
      in the detail panel as incoming "(from another project)" badges.
      Navigation, tooltips and drag-and-drop resolve qualified ends across
      projects (`link_options` gains `from_project_id`; the options name
      the project holding the configuration). Decisions: "structure is
      refused, data cascades" — dropping a reference (or reparenting away
      from an implicit parent) is refused (400) while a link
      configuration targets that project; removing a requirement, purging
      a module or purging a project deletes other projects' link pairs to
      it and refreshes their mirrors, auto-committed in their own repos,
      with a sync-conflict pre-check (409) on every affected project;
      every written project gets its own `changed` broadcast. Exports:
      CSV/XLSX/DOCX write the qualified mirror text; ReqIF writes the
      mirror attribute but no SPEC-RELATION for cross-project links.
      History messages qualify cross-project targets and record reference
      changes. `PROJECT_FORMAT` 5 → 6 (the migration rewrites nothing).
      Endpoints: `GET/PUT /projects/{project_id}/references`,
      `GET /projects/{project_id}/incoming-links?module_id=`; WS
      `project.references`, `project.set_references` (mutating,
      broadcast), `link.incoming`. Backend: `backend/reqman/{models.py,
      service.py, app.py, formats.py, history.py}`; design in
      `docs/ARCHITECTURE.md` ("Project references and cross-project
      links"). UI: References card on the project page ("Referenced by"
      with link counts), References button, sidebar project menu
      "References…", `ProjectReferencesModal`, `LinkConfigurationModal`,
      `RequirementDetailPanel`. Not covered: tests; follow-ups in the
      backlog ("Project references: follow-ups").
- [x] **Module folders** (2026-10-08; requested 2026-10-04). Modules in a
      project are organised into nested folders; folders are purely
      organisational (module ids, requirement ids and links unaffected).
      `PROJECT_FORMAT` 6 → 7: a lossless migration turns the
      `module_order` id strings into `{"module": id}` entries; folders are
      stored as `{"folder", "name", "children"}`; the file is rewritten
      only on the next real save. `ProjectService` rules: server-allocated
      folder ids (unique slug of the name); names non-empty, at most 200
      characters, unique among sibling folders (case-insensitive); only
      empty folders can be deleted; no move into itself or a descendant;
      nesting at most 16 levels (64 tolerated when reading hand-edited
      files); moves take an optional index; no-op moves write nothing;
      `module.reorder` reorders within the module's folder;
      `create_module` takes an optional `folder_id`. History messages for
      every folder operation. Templates: optional `module_tree` key (no
      template format bump, like `row_styles`); `RBC_Template` has the
      folders Requirements (StRS, SyRS, SwRS), Tests (SysTest, SwTest) and
      Test Execution (SysTestExec, SwTestExec); save as template writes
      `module_tree` when the project has a folder. Endpoints:
      `GET /projects/{project_id}/module-tree`;
      `POST /projects/{project_id}/module-folders` (`{name, parent_id?}`);
      `PUT .../module-folders/{folder_id}/name`;
      `DELETE .../module-folders/{folder_id}`;
      `POST .../module-folders/{folder_id}/move` (`{folder_id?, index?}`);
      `POST .../module-folders/{folder_id}/reorder` (`{direction}`);
      `POST /projects/{project_id}/modules/{module_id}/move`;
      `POST /projects/{project_id}/modules` accepts `folder_id`. WS
      `module_tree.get`, `module_folder.create/rename/delete/move/`
      `reorder`, `module.move`, `module.create` with `folder_id`; every
      mutation broadcasts `changed`. Decisions: Test Execution is a
      separate top-level RBC folder; an empty folder is deleted without
      confirmation; grouped pickers list unfoldered modules first, then
      one group per folder path. Backend: `backend/reqman/{models.py,
      service.py, app.py, formats.py, history.py, templates.py,
      templates/rbc_template.json, module_tree.py (new)}`; design in
      `docs/ARCHITECTURE.md` ("Module order and module folders"). UI:
      sidebar Project tab tree with collapsible folders (collapse state
      per browser, `reqman.moduleFolders.collapsed`) and HTML5
      drag-and-drop of modules/folders into folders or onto the project
      name (top level); registry `folder` element type with
      `FOLDER_NODE_MENU` (New module here, New subfolder, Rename folder,
      Move Up/Down, Move to folder…, Delete folder — disabled unless
      empty); project node "New folder"; module node "Move to folder…";
      `ModuleFolderNameModal`, `MoveToFolderModal`, Folder select in
      `NewModuleModal`; link configuration selects (including referenced
      projects) and the traceability chain picker grouped by folder path;
      the attribute "Applies to modules" checklist shows folder paths; the
      project page shows "in <path>" and moves up/down within the folder.
      Not covered: tests; follow-ups in the backlog ("Module folders:
      follow-ups").
- [x] **Module templates + heading skeletons** (2026-10-08; requested
      2026-10-04). Module templates are JSON files with `"kind": "module"`
      and their own format version 1: default name, prefix, description,
      module type, applicable attributes (no `required`, no link
      configurations, nothing cross-project), optional view and a heading
      skeleton. Built-in templates in `backend/reqman/templates/modules/`
      (requirements, test_cases, test_execution with the requested
      chapter lists); user templates in `<data dir>/_templates/modules/`;
      ids `builtin:<stem>` / `user:<stem>` in a namespace separate from
      project templates; a module template file in the project template
      folder (or the reverse) is rejected. Heading skeletons: nested
      `{heading, text?, children?}`, at most 2000 entries, created as
      Object Type = Heading requirements in tree order with server ids and
      defaults; `text` becomes the heading's first child with Object Type
      = Information. Project template format version 2: module entries
      may reference `module_template` (then omit name/prefix) and carry
      their own `skeleton` (`[]` = none); version 1 files are read via an
      in-memory `migrate_v1` and never rewritten; saved/imported templates
      are written as version 2 (older ReqManTool cannot read them). Save
      project as template now also writes module headings as skeletons
      (behaviour change). `RBC_Template` v2: all 7 modules reference
      built-in module templates; SyRS with the RBC chapters (functional,
      interfaces), SwRS with the EN 50128 structure; heading objects are
      created in every module. Add module from template (overrides name/
      prefix/description, `folder_id`; validated in memory, one history
      commit, one broadcast; partial-write behaviour as `create_module`)
      and Save module as template (attributes, optional view, headings;
      the first Information child becomes `text`; rich text → plain;
      duplicate name 409; broadcasts `templates_changed`). Decisions:
      attributes merge by name (case-insensitive) — same type required,
      link mirrors never match, otherwise refused; existing attributes are
      never changed except that their scope is extended to the new module;
      missing ones are added optional and scoped to the new module; enum
      choices are never edited (a missing "Heading"/"Information" Object
      Type choice is refused); Object Type is found by name or id; the
      template view is added to the project unless a same-named view
      exists (not made active). Endpoints: `GET /module-templates`,
      `POST /projects/{project_id}/modules/from-template`,
      `POST /projects/{project_id}/modules/{module_id}/save-as-template`;
      WS `module_template.list`, `module.create_from_template`,
      `module.save_as_template`. Backend: `backend/reqman/{service.py,
      app.py, store.py, templates.py, templates/rbc_template.json,
      templates/modules/ (new)}`; design in `docs/ARCHITECTURE.md`
      ("Module templates and heading skeletons"). UI: lazy template picker
      in `NewModuleModal`; project and folder actions "New module from
      template…", module action "Save module as template…", File menu
      entries; `SaveModuleAsTemplateModal` (new). Not covered: tests;
      follow-ups in the backlog ("Module templates: follow-ups").
- [x] **Commit browser for project, module and requirement** (2026-10-08;
      requested 2026-10-07). One `CommitList` component at three scopes:
      project (all commits), module (`git log` limited to the module
      folder), requirement. Rows: short hash, date, author, message,
      baseline tags, changed-requirement count; "Load more" paging (50);
      filters by author, text and since/until (committer date, server
      time zone, years clamped to 1970–2099). Commit details: full
      message, author/committer, parents, baseline tags, project/module
      change notes (module renames/reorders as text notes from
      `describe_changes`), requirement-level compare against the first
      parent via the baseline compare engine (older-format `project.json`
      migrated in memory), and "Browse project at this commit" read-only
      (`BaselinePage` reused). Full 40/64-hex hashes only (rev
      expressions → 404). Decisions: the requirement History tab keeps
      its inline diffs/blame and links into the browser instead of being
      replaced by `CommitList`; a requirement moved between modules
      counts twice in the changed count; module renames/reorders are
      notes, not structured compare fields. Endpoints:
      `GET /projects/{project_id}/git/commits` (`module_id`,
      `requirement_id`, `offset`, `limit`, `author`, `text`, `since`,
      `until`); `GET .../git/commits/{commit}`;
      `GET .../git/commits/{commit}/snapshot`;
      `GET .../git/commits/{commit}/modules/{module_id}/requirements`; WS
      `commit.list`, `commit.get`, `commit.snapshot`,
      `commit.requirements`; read-only, no broadcast. Backend:
      `backend/reqman/{git_repo.py (GitHistory.list_commits via rev-list
      --first-parent, cat-file --batch, for-each-ref tags, diff-tree -z
      counts; commit_change, commit_files), service.py (git_commits,
      git_commit, get_commit_snapshot, commit_requirements; snapshot
      helpers shared with baselines), app.py}`; design in
      `docs/ARCHITECTURE.md` ("Commit browser"). UI: `CommitBrowserPage`,
      `CommitDetailsPage`, `CommitList` (new); `CompareResult` extracted
      from `BaselineComparePage`; reachable from the Git panel (hash links,
      "Browse all commits"), the requirement History tab ("Show in the
      commit browser", hash links), the View menu, project/module node
      menus and both requirement menus ("Project commits", "Module
      commits", "Requirement commits"). Not covered: tests; follow-ups
      in the backlog ("Commit browser: follow-ups").
- [x] **Project lifecycle: remove project, lock project, baseline
      modules** (2026-10-08; requested 2026-10-04, absorbs the former
      "Baselines" item). Lock: `Project.locked` = `ProjectLock {by, email,
      at, reason, baseline?}` (who from the git commit identity), written
      only when set; PROJECT_FORMAT 7 → 8 (older tools would otherwise drop
      the lock on save). One guard `_require_unlocked` inside
      `_records_history` before any write; only unlock is exempt; errors
      REST 423 / WS -32004. Allowed while locked: reads, exports, reports,
      save as template, create baseline, git enable/push/pull/remote,
      creating a subproject under a locked parent, linking from another
      project into it (stored in the other project). Cascades into a
      locked referencing project (requirement removal, module/project
      purge) are refused 423 before writing. History messages "Project
      locked: <reason>" / "Project unlocked". Remove: typed-name
      confirmation; refused 409 while it has subprojects or referencing
      projects (listed; broken projects count), 423 while locked, 409 if
      the data folder changed (server checks `data_dir`), 400 on name
      mismatch; the folder must be a real directory directly in the data
      folder (no symlink/junction), renamed into `.reqman-staging/` then
      deleted. The project's `.git` is inside its folder, so history and
      baselines go too; a server-written recovery hint by git state
      (remote: clone back + what is unpushed; nested in another repo:
      restore from it; else: no recovery without a backup). Broadcast
      `{"event":"changed","project_id":…,"removed":true}`; clients close
      its tabs, clear the clipboard and navigate away. Baseline glue:
      create baseline accepts `lock`/`lock_reason` (locks first, then
      tags; the lock is undone if the baseline fails); verification and
      execution reports and their DOCX/XLSX exports carry `data_source`
      ("Data: … = baseline v1"). Endpoints: `PUT/DELETE
      /projects/{project_id}/lock`, `GET /projects/{project_id}/removal`,
      `DELETE /projects/{project_id}` (`{confirm_name, data_dir}`),
      baselines POST `lock`, `lock_reason`; WS `project.lock`,
      `project.unlock`, `project.removal_info`, `project.remove`.
      Backend: `backend/reqman/{models.py, service.py, store.py, app.py,
      history.py, git_repo.py, report_export.py, formats.py}`; design in
      `docs/ARCHITECTURE.md` ("Project lifecycle: lock and remove"). UI:
      `LockProjectModal`, `RemoveProjectModal`, `ProjectLockBadge`,
      `projectLock.ts` (new); lock badge on the project page, module page
      and sidebar, lock alert, Lock/Unlock buttons and menu entries,
      Remove project in the project menus; `edits: true` on 37
      data-changing registry actions disables them while locked, as are
      inline editing, the detail panel, drag-and-drop and row actions;
      "Lock after baseline" in `CreateBaselineModal`. Not covered: tests,
      browser verification; follow-ups in the backlog ("Project
      lifecycle: follow-ups").
- [x] **AI prompt library (copy → external AI chat → CSV → import)**
      (2026-10-08; requested 2026-10-04). ReqManTool never calls an AI
      service: the user copies a generated prompt into any AI chat and
      imports the CSV answer. Prompts are JSON files: 7 built-in in
      `backend/reqman/prompts/` (draft-requirements,
      decompose-strs-to-syrs, derive-swrs-from-syrs, generate-test-cases,
      split-compound-requirements, rewrite-ears-incose,
      review-ambiguities), read-only (duplicate to change); user prompts
      in `<data>/_templates/prompts/` (never listed as a project) can be
      created, edited, deleted, exported and imported. Ids `builtin:…` /
      `user:…`; an unknown `{{placeholder}}` makes a prompt invalid.
      Placeholders are filled server-side: project/module name and
      description, the module's exact CSV import header (types, required
      flags, enum choices), the selected requirements as CSV ("selected"
      = one requirement, optionally with children, or the whole module)
      and the import rules; the prompt is generated on "Generate
      preview", not live. CSV import extensions: a code fence is stripped
      if it is the first non-blank line; `Link: <configuration name or
      id>` columns create links validated against the module's link
      configurations (incl. cross-project, qualified `proj:ID`; a bare
      config-name column, the export's mirror column, stays ignored so
      exports re-import unchanged); update mode ("Update existing (match
      Id)"): empty cell = unchanged, Parent must be empty or the current
      parent (never moves), rich text whose plain text equals the cell is
      unchanged, unknown Ids are created; create mode keeps the old
      Parent rule. A read-only preview shows new rows, per-attribute
      old/new diffs and links; import is enabled only after previewing
      the current input; all-or-nothing as before; respects the project
      lock (preview allowed). Endpoints: `GET/POST /prompts`, `POST
      /prompts/import`, `GET/PUT/DELETE /prompts/{id}`, `GET
      /prompts/{id}/export`, `POST /projects/{pid}/prompts/render`,
      `POST /projects/{pid}/modules/{mid}/import/csv/preview?mode=
      create|update`, `POST .../import/csv?mode=`; WS `prompt.list`,
      `prompt.get`, `prompt.create`, `prompt.update`, `prompt.delete`,
      `prompt.import`, `prompt.export`, `prompt.render`,
      `requirement.preview_csv_import`, `requirement.import_csv` `mode`;
      prompt mutations broadcast `prompts_changed`. Backend:
      `backend/reqman/{prompts.py, prompts/, csv_import.py, service.py,
      store.py, app.py}`; design in `docs/ARCHITECTURE.md` ("AI prompt
      library and CSV import extensions"). UI: `PromptsModal` (Use tab:
      pick, free text, Generate preview, Copy; Manage tab) and
      `promptActions.ts` (new); Tools menu and "AI prompts" in the
      requirement and module context menus; a one-time per-browser
      notice before Copy that requirements leave the machine; "Paste AI
      result" opens `ImportCsvModal` with a paste-text box. Not covered:
      tests, browser verification; follow-ups in the backlog ("AI prompt
      library: follow-ups").
- [x] **GitLab and GitHub API integration, parts (1)–(3)** (2026-10-08;
      requested 2026-10-07). Provider connections (GitLab.com /
      self-hosted, GitHub.com / Enterprise via `/api/v3`) are stored in
      `connections.json` beside the config file (own `format_version`;
      not in `config.json`, which `save_data_dir` rewrites); tokens live
      in the OS keychain (new dependency `keyring>=25.0`), cached in
      process memory, never in project data, git, config, logs or
      responses; a token is checked with `GET /user` on add;
      `REQMAN_TOKEN_STORE=memory` for tests. One provider abstraction
      (GitLab REST v4, GitHub REST) over stdlib urllib: 20 s timeout,
      8 MiB response cap, redirects never followed, provider errors
      verbatim; plain-http non-loopback URLs refused. Server-edition
      OAuth hook point: `TokenStore` / `ProviderService.token`. Git auth:
      the token is passed only in the git process environment
      (`GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`, `http.<connection
      URL>/.extraHeader` = Authorization Basic, `followRedirects=false`),
      scoped to the connection URL, never on the command line, in
      `.git/config` or in the remote URL; covers ls-remote, fetch, push,
      clone and auto-push; needs git ≥ 2.31. Publish project creates the
      repository in a chosen namespace (private by default), sets the
      remote and pushes (409 if already published). Add project from
      GitLab/GitHub browses/searches repositories and clones one (GitHub
      search scans at most 500 recently updated repositories). Review
      workflow: push to a review branch and open an MR/PR targeting
      `current` with a generated description (changed requirements,
      baseline reference); status, approvals and merge status in the Git
      panel; refresh/forget; tracked per instance in `.git/config`
      `reqman.review.*`; GitLab MRs set `remove_source_branch: true`.
      Decisions: publish/review are git remote operations and work while
      a project is locked (like push/pull); review always targets
      `current` and asks for a merge commit/fast-forward (squash would
      cause a sync conflict on the next pull); auto-push still pushes
      `current` directly (bypasses review, user's choice). Endpoints:
      `GET/POST /providers/connections`, `PUT/DELETE
      /providers/connections/{id}`, `GET .../{id}/namespaces`, `GET
      .../{id}/repositories?search=&page=`, `POST /projects/from-provider`,
      `POST /projects/{id}/git/publish`, `GET /projects/{id}/git/provider`,
      `GET|POST|DELETE /projects/{id}/git/review`; WS
      `provider.connections`, `provider.add_connection`,
      `provider.update_connection`, `provider.remove_connection`,
      `provider.namespaces`, `provider.repositories`,
      `project.create_from_provider`, `project.git_publish`,
      `project.git_provider`, `project.git_review_status`,
      `project.git_review_start`, `project.git_review_forget`; broadcast
      `connections_changed`. Backend: `backend/reqman/{providers.py,
      service.py, git_repo.py, config.py, formats.py, app.py}`; design in
      `docs/ARCHITECTURE.md` ("GitLab and GitHub integration"). UI:
      `ProviderConnectionsModal` (Tools menu), File ▸ Add project from
      GitLab/GitHub and a HomePage button (`AddProjectFromProviderModal`),
      Git panel "Publish to GitLab / GitHub…" (`PublishProjectModal`) and
      Review section (`StartReviewModal`), `ProviderConnectionSelect`,
      `providerActions.ts` (new). Not covered: part (4) issue links,
      tests, browser verification; follow-ups in the backlog
      ("GitLab/GitHub: follow-ups").
- [x] **Distribution package + user-space install** (2026-10-08;
      requested 2026-10-04). Packaging: `backend/pyproject.toml`
      (setuptools, distribution `reqmantool`, `license =
      "AGPL-3.0-only"`, version from `reqman.__version__`,
      `requires-python = ">=3.11"`, console script `reqman`, `test` extra
      with pytest/httpx; package data: templates, prompts, `static/**`,
      `BUILD`); `backend/requirements.txt` is runtime only (uvicorn now
      listed), pytest/httpx moved to `backend/requirements-dev.txt`. CLI
      (`reqman`, `python -m reqman`): `reqman [serve] [--dir|--data-dir]
      [--host] [--port] [--cors-origins] [--config]`, `reqman init`
      (per-user config file and data dir, no root/admin), `reqman config`
      (effective settings with their sources), `reqman service
      install|uninstall [--dry-run] [--no-activate]` (systemd `--user`
      unit on Linux, launchd agent `io.github.reqmantool.reqman` on
      macOS), `--version`; `etc/start-backend.sh` runs `python -m reqman
      serve`. Config: precedence CLI > env (`REQMAN_CONFIG_FILE`,
      `REQMAN_DATA_DIR`, `REQMAN_CORS_ORIGINS`, new `REQMAN_HOST`,
      `REQMAN_PORT`, `REQMAN_FRONTEND_DIR`) > config file > platform
      default; keys `data_dir`, `host`, `port`, `cors_origins`;
      `save_data_dir` keeps the other keys in stable order. Config lookup:
      `REQMAN_CONFIG_FILE`/`--config`, else the checkout's
      `.reqman/config.json` when running from a git checkout and it exists
      (backward compatible, no migration), else the per-user file (Linux
      `$XDG_CONFIG_HOME/reqman/config.json`, macOS `~/Library/Application
      Support/ReqManTool/config.json`, Windows `%APPDATA%\ReqManTool\`);
      data defaults: checkout `example_database/`, installed the per-user
      data dir (Linux `$XDG_DATA_HOME/reqman`, macOS `…/ReqManTool/data`,
      Windows `%APPDATA%\ReqManTool\data`). No CONFIG_FORMAT bump
      (optional keys). Same-origin serving: the bundled frontend
      (`reqman/static`) is served by FastAPI with an SPA fallback
      middleware (HTML navigations only; API, `/ws` and downloads are
      never shadowed); a checkout backend does not auto-serve
      `frontend/dist`; the frontend API/WS base defaults to the page
      origin in production builds, `localhost:8899` in the Vite dev
      server, `VITE_*` overrides kept; Vite preview proxy extended.
      Version: build id from env, packaged `reqman/BUILD`, git, or `dev`
      (existing `GET /version` and footer). GitHub Actions:
      `.github/workflows/ci.yml` (pytest on Python 3.11 and 3.14, frontend
      npm ci/build/lint on Node 24), `.github/workflows/release.yml` (on a
      `v*` tag that must equal `v<__version__>`: `etc/build-dist.sh`,
      wheel smoke install, `gh release create` with wheel + sdist);
      `etc/build-dist.sh` new. Backend: `backend/reqman/{cli.py,
      __main__.py, service_unit.py, config.py, app.py, version.py,
      formats.py}`; design in `docs/ARCHITECTURE.md` ("Packaging,
      configuration and install"); install section in `README.md`,
      `docs/CLOUDFLARE.md` updated. UI: no new screen (frontend API/WS
      base URL in `frontend/src/api/client.ts`, `frontend/vite.config.ts`).
      Smoke-checked: wheel built and installed in a temp venv with a temp
      HOME (init/config/serve, SPA served, `/version`); plist passes
      `plutil`; no service actually installed. Not covered: tests, CI
      never run on GitHub; follow-ups in the backlog ("Distribution
      package: follow-ups").
- [x] **Two-step linking from the requirement menu: "Start link" → "Make
      link"** (2026-10-08; requested 2026-10-08). Frontend only (existing
      link API, no backend change). `requirement.startLink` remembers the
      requirement as the pending link source in localStorage
      `reqman.pendingLink` (`{dataDir, source{projectId, moduleId,
      requirementId, names}}`), synced across main tabs and detached
      windows via the `storage` event (a source from another data folder
      is ignored); "Start link" on another requirement replaces it.
      `requirement.makeLink` (hidden while nothing is pending, disabled on
      the source, `edits: true` so disabled when the target project is
      locked, like drag-and-drop) resolves through `resolveAndLink`,
      extracted from `useDropLink` and shared with drag-and-drop
      (`link_options` with `from_project_id` across projects; one → created
      in the configured direction; several → `DropLinkModal` picker; none
      → warning; existing → reported); the pending source is cleared only
      after a successful link (it stays after none/exists/error/cancel so
      another target can be picked). `edit.cancelLink` (Esc, not while
      typing or while a menu/dialog is open). It is also cleared on project
      removal and data-folder change. All three in the requirement
      right-click menu and the Edit menu after "Link to". UI:
      `PendingLinkBar` ("Linking from SYRS-0012 (Project · Module when
      different) — pick a target requirement: right-click it → Make link
      (Esc to cancel)" + "Cancel link") between the module tab bar and the
      page, and below the detached-window header. Files:
      `frontend/src/actions/{pendingLink.ts (new), AppActionsProvider.tsx,
      registry.ts, requirementActions.tsx, shortcuts.ts, types.ts}`,
      `components/DropLinkModal.tsx` (`onLinked`), `hooks/useDropLink.ts`,
      `layout/{PendingLinkBar.tsx (new), AppLayout.tsx, DetachedLayout.tsx,
      AppDialogs.tsx}`, `index.css`. Not covered: tests, browser
      verification; follow-ups in the backlog ("Two-step linking:
      follow-ups").
- [x] **AGPL compliance in the app** (2026-10-08; follow-up of the
      2026-10-06 licence change). AGPL §13 source offer: a "Source code"
      link in the footer and Help ▸ About from the new `GET /about` (WS
      `about.get`) → `{version, build, license, source_url,
      source_ref|null, source_link}`; `GET /version` unchanged. Setting
      `source_url`: env `REQMAN_SOURCE_URL` > config key `source_url` >
      default `https://github.com/bula173/ReqManTool`; must be http(s)
      (startup fails otherwise); trailing `/`/`.git` stripped. Ref: a
      commit build id → that commit; tag-like → the tag; `dev`, `-dirty`,
      another `REQMAN_BUILD` or a checkout with uncommitted tracked
      changes → repo root; link `<source_url>/tree/<ref>`. The frontend
      falls back to the upstream URL if the backend is unreachable;
      `etc/build-dist.sh` marks dirty builds `-dirty`. About dialog:
      Source code and Licence (`AGPL-3.0-only`) rows, AGPL notice
      (copyright 2026 Marcin Kwiatkowski, no-warranty wording, commercial
      licence / `LICENSING.md`), note that operators of modified versions
      must publish their source and set `source_url`; stale "(MIT
      license)" text removed. Third-party licences: Vite plugin
      `frontend/plugins/thirdPartyLicenses.ts` (no new dependency) emits
      `dist/assets/third-party-licenses.json` from production deps with
      licence texts (warns, never fails the build); the backend list is
      computed at runtime from installed metadata (`GET
      /about/third-party`, WS `about.third_party`). SPDX `AGPL-3.0-only`
      in `frontend/package.json` (+ package-lock root); `pyproject.toml`
      already had it. `CONTRIBUTING.md` (new) with the CLA requirement;
      `LICENSING.md`, `README.md`, `docs/ARCHITECTURE.md` updated.
      Backend: `backend/reqman/{licenses.py (new), version.py, config.py,
      app.py}`. UI: `AboutModal` (Help ▸ About), `VersionFooter`,
      `ThirdPartyLicensesModal` (new; Frontend/Backend tabs, filter,
      expandable texts, opened from About), `hooks/useSourceCode.ts`
      (new). Not covered: tests, CLA text; follow-ups in the backlog
      ("AGPL compliance: follow-ups").
- [x] **Reorder table columns by dragging their headers.** Attribute
      column headers of the module table and the baseline/commit table can
      be dragged left/right by their label (pointer events; a vertical drop
      indicator shows the landing spot, Esc cancels, the table scrolls near
      its edges); the right-edge resize handle keeps resizing and the Id
      column stays first. Keyboard alternative: "Move column left/right" in
      the column header menus. The order is per browser, per project +
      module + view, stored with the column widths
      (`hooks/columnLayout.ts`, `order`); "Reset column order" sits next
      to "Reset column widths". Not covered: saving the order to a named
      view (no update-view endpoint for `View.attribute_ids` yet).
- [x] **Server edition: login (GitLab / OIDC), users, project membership,
      module edit locks** (2026-10-08; requested 2026-10-06, replaces the
      2026-10-04 "User handling" item). Delivered in four slices (commits
      9c99c78, 92dfe27, 86d9a76, plus slice 4); design in
      `docs/ARCHITECTURE.md` ("Server edition"). Slice 1, mode and login:
      `mode: local|server` (env `REQMAN_MODE` > config `mode` > local); the
      local edition is unchanged and never imports server code. Server-only
      package `backend/reqman_server/` (not in the AGPL wheel, runs from a
      checkout); core extension points in `backend/reqman/extensions.py`
      (`RequestIdentity` ContextVar, `PermissionGuard`, `ModuleLockService`,
      `ProjectEvents`, `rpc_methods`, audit). GitLab and generic OIDC
      login (discovery, PKCE, state bound to a browser cookie, nonce; the
      ID token comes from the token endpoint over TLS and is validated for
      iss/aud/azp/exp/iat/nonce, no signature check per OIDC Core
      §3.1.3.7; no new dependency); server-side sessions (HttpOnly,
      SameSite=Lax, Secure configurable, idle and max age, `sessions.json`
      stores token hashes); CSRF synchronizer token `X-CSRF-Token`;
      WebSocket origin check; 401 / WS close 4401; `users.json` (the first
      user or `server.admins` become admin); git commits authored as the
      logged-in user. Routes `GET /edition`, `/auth/login/{p}`,
      `/auth/callback/{p}`, `/auth/me`, `POST /auth/logout`, `/health`.
      Non-admins cannot change the data folder or use `/fs`; `/config` is
      redacted. Slice 2, membership: `memberships.json` (viewer / editor /
      project-admin; server admins may do everything; group → role rules
      from the `groups` claim; `server.project_creation` everyone|admins,
      the creator becomes project admin); one guard wrapping the
      `ProjectService` methods (403 / WS -32003, checked before the
      project lock); project list and live-sync events filtered per user;
      `permissions_changed` event; admin API `/admin/users`,
      `/admin/projects/{id}/members` + WS `admin.*`; provider connections
      and remote URLs admin-only in server mode. Slice 3, module edit
      locks: `module_locks.json` (owner, since, reason, idle expiry
      `server.module_lock_idle_minutes`, default 60; optional
      `server.require_module_checkout`); writes by others refused with 423
      / WS -32005; links checked on the link configuration's source
      module; structure operations (move/reorder/folders) and project-level
      changes not covered; admins must break a lock (project-admin right);
      locks released on logout (when no other session) and on
      deactivation; `module_locks_changed` event; REST `GET
      /projects/{id}/module-locks`, `PUT/DELETE
      /projects/{id}/modules/{m}/lock`, `POST .../lock/break`, WS
      `module.locks/lock/unlock/break_lock`. Slice 4, attribution and
      audit: per-user squash window through a `ReqMan-User: <id>` commit
      trailer in server mode (local commits unchanged); created_by /
      modified_by computed from git history (`created` / `modified` in the
      requirement history response), not stored, no format bump; `changed`
      events carry `by: {user_id, name}` in server mode; append-only
      `audit.jsonl` (login, failed login, logout, admin user/member
      changes, lock break, project create/lock/unlock/remove/purge) with
      admin-only `GET /admin/audit` (paging by byte offset, filters) + WS
      `admin.audit.list`. Decisions: a 403 reveals that a hidden project
      exists; memberships and locks are keyed by project id, not by data
      folder; no ID-token signature check (see above). Backend:
      `backend/reqman/{extensions.py, app.py, service.py, git_repo.py,
      history.py}`, `backend/reqman_server/` (incl. `routes.py`,
      `admin.py`, `module_locks.py`, `audit.py`, `settings.py`). UI:
      `AuthGate`, `LoginPage`, `UserMenu` (role badge), `AdminModal`
      (users, project members, Audit log tab), editing disabled for
      viewers, module lock badges with check-out / release / break
      actions, created/modified by in `RequirementDetailPanel`. Not
      covered: LDAP and reverse-proxy providers, licence-file gate, tests;
      follow-ups in the backlog ("Server edition: follow-ups").
- [x] **Views as portable project elements** (2026-10-08). A view's
      column order, widths and wrap live in the view in `project.json`
      instead of per browser, so they travel with the project. New optional
      `View.single_line` (written only when set; no PROJECT_FORMAT bump,
      like `column_widths`); `attribute_ids` is the column order.
      `ProjectService.update_view` (name, ordered `attribute_ids`,
      `column_widths`, `single_line`; any subset; validated before writing,
      removed columns pruned); REST `PUT /projects/{p}/views/{v}`, WebSocket
      `view.update`, broadcast `changed`; `view.create` / `POST .../views`
      take `single_line` and reject duplicate columns. Project and module
      templates (and Save as template) carry the order plus optional
      `column_widths` / `single_line`. Frontend: module and baseline pages
      show a view's columns in its order with its widths and wrap (baseline:
      new view picker over the snapshot's views); local changes in a named
      view are an "Unsaved layout" draft with "Save layout to view",
      "Revert to saved view" and "Save current layout as new view…" (View
      menu, column header menu, toolbar); "All attributes" stays per
      browser; live sync shows saved changes to other clients. The tree
      indent / expand arrow is always in the Object Text column. View
      applicability: optional `View.modules` (module ids, written only when
      non-empty, kept in module order; settable on create and update;
      project templates carry it as module keys); module pages offer only
      applicable views. View editor dialog (View ▸ Edit view, pencil by the
      view selector, project page views list): rename, columns with up/down
      order, per-column wrap, "Applies to modules", delete with
      confirmation. Files:
      `backend/reqman/{models.py, service.py, app.py, templates.py}`,
      `backend/tests/test_view_update.py` (new),
      `frontend/src/hooks/columnLayout.ts`, `pages/{ModulePage.tsx,
      BaselinePage.tsx, ProjectPage.tsx}`,
      `components/{SaveLayoutAsViewModal.tsx, ViewEditorModal.tsx}` (new),
      `api/applicability.ts`,
      `actions/{appActions.ts, registry.ts, types.ts, columnActions.ts,
      AppActionsProvider.tsx}`, `layout/AppDialogs.tsx`, `api/{types.ts,
      queries.ts}`, `docs/ARCHITECTURE.md`. Not covered: no frontend tests
      (no test runner); no browser smoke test; "Save column widths to
      view" was replaced by "Save layout to view".
- [x] **Server edition: roles from GitLab/GitHub repository permissions,
      reviewer role and status guards** (2026-10-08; requested
      2026-10-08, slice 5 of the server edition); design in
      `docs/ARCHITECTURE.md` ("Roles from repository permissions, reviewer
      and status guards (slice 5)"). A project is bound to a user when its
      git remote is on the instance the user logged in with (https by
      host/port/path prefix, ssh/scp by host, provider `git_hosts`
      aliases); the role comes from the provider API with the user's OAuth
      token: GitLab `GET /api/v4/projects/:path` permissions plus
      `/members/all/:user_id` (inherited), GitHub `GET /repos/{o}/{r}`
      permissions. Mapping: GitLab Guest / Reporter / Planner, GitHub Read
      → viewer; GitHub Triage → reviewer; GitLab Developer, GitHub Write →
      editor; GitLab Maintainer / Owner, GitHub Maintain / Admin →
      project-admin; visible but not a member → viewer; invisible
      repository → no role. Cache per (user, project)
      `server.repo_roles.cache_minutes` (5), resolved at login (8 in
      parallel), failed lookups retried after 30 s, last good role kept
      for `grace_minutes` (60). Tokens (access, refresh, expiry) only in
      the server-side session (`sessions.json`, 0600), refreshed once on
      expiry/401; GitLab logins add the `read_api` scope when repo_roles
      is on. New GitHub OAuth2 login provider (`type: "github"`, state +
      PKCE, `/user` and `/user/emails`, orgs as groups). New role
      **reviewer** (viewer < reviewer < editor < project-admin) in role
      lists, group roles, memberships and admin pickers. Status guards in
      `ProjectService` (server mode only; status attribute found by
      name): a reviewer may change only the status; → Reviewed needs
      reviewer rank and independence (not the last content author, by
      e-mail then name; refused without git history or with uncommitted
      changes); to/from Approved needs project-admin and only from
      Reviewed; applies to update, add and CSV import. Project-admin only:
      baselines, attribute definitions/scope, views, link configurations
      (lock/unlock already). Errors name the role and its source ("needs
      Developer on gitlab.example.com/rbc/rbc-gp (you have Reporter …)"),
      403 / WS -32003. API: `POST /auth/permissions/sync`, `POST
      /admin/access/sync {project_id?}`, WS `auth.permissions.sync`,
      `admin.access.sync`, broadcast `permissions_changed`; `/auth/me`
      permissions gain `repo_roles` and nullable roles; the members list
      gains repository entries. Config `server.repo_roles {enabled,
      cache_minutes, grace_minutes}`, provider `type: github`,
      `git_hosts`. Core hooks: `ACTION_PROJECT_REVIEW`,
      `ACTION_PROJECT_APPROVE`, `EditionContext.project_ids` /
      `project_remote_url`, `GitHistory.remote_url`,
      `providers.repository_permission`. Decisions: the repository
      permission wins over in-app membership for bound projects (both
      directions); server-admin stays in-app; no sign-off without history;
      approval only from Reviewed and revoking it needs project-admin;
      sync bypasses the cache but keeps the last good role within grace;
      old sessions need a fresh login; `read_api` may trigger a new
      consent prompt. Backend: `backend/reqman/{extensions.py, app.py,
      service.py, git_repo.py, providers.py}`, `backend/reqman_server/`
      (`repo_access.py` and `github_oauth.py` new; `admin.py`, `oidc.py`,
      `permissions.py`, `routes.py`, `sessions.py`, `settings.py`). UI:
      `LoginPage` (GitHub), `AdminModal` ("Sync access now", reviewer in
      pickers, repository members), `ProjectRoleBadge` (shows the source;
      orange when the last check failed), status-only editing for
      reviewers in `ModulePage`, project configuration (attributes, link
      configurations, views) disabled below project-admin in
      `ProjectPage`. Not covered: tests;
      follow-ups in the backlog ("Repository-permission roles:
      follow-ups").
- [x] **Server edition: discover projects in the logged-in user's GitHub /
      GitLab repositories, slice 1 (GitHub OAuth App + GitLab)**
      (2026-10-08). Users logged in through a `github` / `gitlab` provider
      see "Available on GitHub/GitLab" in the Workspace tab: repositories
      they can access tagged with the topic `reqmantool`, plus repositories
      whose root `project.json` on the `current` branch parses as a
      ReqManTool project (background scan per user, cached
      `server.discovery.cache_minutes` (15), at most `max_repos` (200)
      repositories, refresh button, provider errors verbatim). "Open" has
      the server clone the repository once into its data directory
      (shared; a later open fetches and fast-forwards), authenticated with
      the user's OAuth token for those git processes only
      (`git_repo.remote_auth_scope`, `GIT_CONFIG_*` environment; never in
      the repository configuration, the remote URL, a log or a response).
      Opening needs at least GitHub Read / GitLab Guest; the opener is not
      made a member, access follows the repository-permission roles (so
      discovery needs `server.repo_roles`). "Publish project" tags the new
      repository `reqmantool` (GitLab on creation, GitHub with a second
      call; a failure is reported as `topic_error`, publishing still
      succeeds). API: `GET /discovery/projects`, `POST
      /discovery/refresh`, `POST /discovery/open {provider, repo}`, WS
      `discovery.list` / `discovery.refresh` / `discovery.open`, broadcast
      `changed` on open. Core: `ProjectService.open_repository_project`,
      `EditionContext.open_repository`, `providers.git_auth_config`,
      `Provider.get` / `add_topic`, `PROJECT_TOPIC`. Backend:
      `backend/reqman_server/discovery.py` (new), `settings.py`,
      `repo_access.py`, `__init__.py`; `backend/reqman/{service.py,
      git_repo.py, providers.py, extensions.py, app.py}`. UI:
      `DiscoveredProjects.tsx` in the Workspace tab, `api/discovery.ts`,
      live-sync refetch on `changed`. Docs: SERVER_SETUP.md section 8
      (GitHub `repo` scope for private repositories, GitLab `read_api`).
      Tests: `backend/tests/test_server_discovery.py` (smoke; provider HTTP
      mocked, local bare repository as the remote). Left over: GitHub App
      provider type (backlog).
- [x] **Operational Scenarios module type and chapter templates for every
      module** (2026-10-08). New module type `operational_scenarios`
      (`MODULE_TYPES`, project format 9: an older ReqManTool would read such
      modules as requirements modules and drop the type on save; the 8 → 9
      step drops `operational_scenarios` from attribute `module_types`).
      Link type pairs added: requirements → operational_scenarios ("derived
      from"), test_cases → operational_scenarios (scenario-based tests) and
      operational_scenarios → operational_scenarios (refines). Traceability
      reports scenarios not reached by a later test_cases module as
      unverified (`TRACE_VERIFIED_TYPES`); the verification report stays
      requirements-only. Built-in module template
      `builtin:operational_scenarios` (Actors, Trigger, Preconditions,
      Scenario Steps, Postconditions; chapters Introduction, Actors and
      Roles, Normal / Degraded Operation, Emergency, Maintenance).
      RBC_Template: OpScen module first (prefix OPS), "Derived from OpScen"
      (StRS → OpScen), scenario attributes scoped to it (Preconditions
      shared with the test modules, Operational Level/Mode enum), an
      Operational Scenarios view scoped to it, and RBC/CENELEC chapter
      skeletons for every module (new for StRS, SysTest/SwTest and the test
      execution campaigns; SyRS gains Data Preparation). "Apply chapter
      template…" for existing modules: `POST
      /projects/{p}/modules/{m}/apply-skeleton` (+ `/preview`), WS
      `module.apply_skeleton` / `module.preview_skeleton`; adds only
      headings whose title is missing at that level, after the existing
      ones; idempotent; never moves or changes existing requirements.
      Frontend: type option and route icon, link pairs, `ApplySkeletonModal`
      (template picker, preview list) in the module menu and Edit menu.
      Tests: `backend/tests/test_operational_scenarios.py`, stale RBC/type
      expectations updated.
