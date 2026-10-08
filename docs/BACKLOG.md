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
✅ and **user** (who) ❌, requirement templates (pre-filled attribute sets for
a new requirement) ❌, attribute schema evolution (rename/retire enum
choices with migration, delete attributes, rename/delete modules/projects)
and a multi-valued enum ❌, requirement reuse from an ancestor project with
origin/drift tracking ❌.

**Traceability** — links between modules ✅, link direction enforced by a
configured link type per module pair ✅, **suspect links** (a link is
flagged when either endpoint's content changes after the link was made, so
reviewers know to re-verify it) ❌, **traceability matrix** view (grid of
source × target requirements showing which cells are linked) ✅,
**coverage analysis** (% of a module's requirements with at least one
outgoing/incoming link of a given type — finds orphaned requirements) ✅,
**impact analysis** (transitive upstream/downstream walk) ❌, source/test-
code tag traceability ❌, versioned reference-document citations (e.g.
UNISIG subsets) ❌, safety registers — hazard log with closure status, SRAC
register propagated down the project tree ❌.

**History & baselines** — per-object change history (who/when/what changed)
✅ via per-project git repos (auto-commit, per-requirement History tab and
per-attribute blame in the detail panel), **baselines**
(named, immutable snapshots of a module at a point in time, comparable to
each other with a diff view) — this is DOORS' formal equivalent of "tag a
commit," genuinely useful even with git underneath since it's project-data-
aware (which requirements changed, not just which files) ✅ via annotated
`baseline/<name>` git tags + the `baseline` branch, with a
requirement-level compare view.

**Views, filtering & search** — sort/filter the requirements table by
attribute value ❌, saved/named views (a stored filter + column set) ✅,
full-text search across a module or project ❌, global find-and-replace ❌.

**Import / export** — ReqIF export ✅ (see limitations noted above), CSV
export ✅, Excel export ✅, Word export ✅, CSV import ✅, ReqIF import ❌,
test-result import (JUnit XML / Robot Framework) ✅.

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

- [ ] Git integration follow-ups from slice 2a (2026-10-04): git runs
      synchronously on the event loop (about 11–12 git processes,
      ~80–200 ms per mutation; CSV import of 3000 requirements 17 s →
      20 s) and sessions have no maximum length — decide on a session
      cap and/or moving git work off the event loop; `git merge
      --squash` (SQUASH_MSG) does not pause history; separate git dirs
      and linked worktrees are refused (`foreign`), supporting them is
      open; Windows: with no private hooks dir it falls back to
      `/dev/null`, which resolves under the drive root (report an error
      instead?), and `os.path.isjunction` needs Python 3.12+ (no
      `requires-python` pin); a settings UI and config keys for the git
      settings (needs a CONFIG_FORMAT bump with migration); raw
      `git blame` fallback; author from Cloudflare Access once user
      handling exists (remotes/push: done, see "Git remotes" in Done).
      The user's current data dir inside the ReqManTool checkout
      shows `nested`, so it gets no history until moved.
- [ ] Git baselines: review follow-ups (2026-10-05, from the slice 2b
      review). Browse reads the whole baseline tree into memory per
      request — scope `ls-tree`/`cat-file` to the requested module;
      hand-made nested tags like `baseline/x/y` are listed but not
      browsable (filter or mark them); done 2026-10-08: a committed data
      file with a newline in its name silently vanished from
      browse/compare (`cat-file --batch` line protocol) —
      `baseline_files` now reads blobs by object id via a shared
      NUL-safe `_tree_files`; still open: the same issue in 2a's
      pending-changes specs; a hand commit whose subject contains
      `\x1f`/`\x1e` corrupts that git-log entry (handled in the commit
      browser; the existing git-log entry path unchanged); `moduleId`
      is not URL-encoded in the baseline
      requirements query; mutations refetch active immutable baseline
      snapshots despite `staleTime: Infinity`; a description whose final
      paragraph looks like `Reqman-*:` trailers is swallowed when
      listing.
- [ ] Git remotes: review follow-ups (2026-10-06), all nits. (1) Done
      2026-10-07: the remote-URL validator regexes required a letter
      first, but git also allows a digit (`1abc::x`, `1foo://x` passed
      validation and were only blocked by `GIT_ALLOW_PROTOCOL` with git's
      raw error) — use `^[A-Za-z0-9][A-Za-z0-9+.-]*` and add tests.
      (2) Done 2026-10-07: a local/`file://` remote set outside the app
      failed Push/Pull/auto-push with git's raw "transport 'file' not
      allowed" — map it to a clear message. (3) `GitStatusPanel`
      RemoteSync keeps its `editing`/`url` local state when live sync
      changes the remote while the form is open — reset on a `remote.url`
      change (cosmetic). (4) Auto-push runs synchronously inside the
      mutation (10 s timeout) — consider pushing in the background so an
      unreachable remote never stalls edits. (5) Product decisions:
      local paths / shared-filesystem remotes — resolved 2026-10-07,
      allowed (see "Local-folder / network-share remotes" in Done);
      auto-push disabling session squashing — accepted as is.
      (6) `_is_non_fast_forward` matches "[rejected]" but not "[remote
      rejected]", so some remote rejections map to 400 instead of 409.
- [ ] Rich text slice 2: follow-ups (2026-10-08). (1) Tests for the
      slice 2 exports (DOCX formatting, ReqIF XHTML typing) are pending;
      `tests/test_rich_text.py::test_exports_write_the_plain_projection`
      still expects plain ReqIF and must be updated. (2) No ReqIF
      importer reads the XHTML back yet. (3) Later slice: rich-text
      tables and images.
- [ ] Commit browser: follow-ups (2026-10-08). (1) Structured module
      rename/reorder/folder entries in the compare engine (today shown as
      text notes from `describe_changes`). (2) Text search matching hash
      prefixes. (3) Total commit count. (4) Requirement picker on the
      browser page. (5) Stale filter inputs when a menu action navigates
      to the same scope (cosmetic). (6) Browse-at-commit reads the whole
      tree into memory (as baselines). (7) Tests pending (phase B).
- [ ] GitLab and GitHub API integration. Requested 2026-10-07. Builds on
      git remotes (done). Provider connections (GitLab.com / self-hosted
      GitLab URL, GitHub.com / GitHub Enterprise URL) authenticated with a
      personal access token in the local edition (stored in the OS
      keychain, never in project data or git) and with the user's OAuth
      login in the server edition. Features: (1) "Publish project" — create
      the remote repository (choose group/organisation, name, private by
      default) and set it as the project's remote, then push; (2) "Add
      project from GitLab/GitHub" — browse/search the user's repositories
      and clone one (instead of pasting a URL); (3) review workflow —
      push the current work to a review branch and open a merge request /
      pull request with a generated description (changed requirements,
      baseline reference), show its status and approvals in the Git panel;
      (4) optional: link requirements to issues (issue URL attribute,
      create issue from a requirement). All API calls server-side through
      one provider abstraction (GitLab REST v4, GitHub REST), REST +
      WebSocket parity, provider errors shown verbatim, no token ever sent
      to the browser.
- [ ] Server edition: login (GitLab / OIDC / LDAP), users, project
      membership, module edit locks. Requested 2026-10-06 (replaces the
      2026-10-04 "User handling" item; scope change confirmed by the user).
      Two editions from one codebase: **local** (free) — today's single-
      user mode, no login, identity from git config, must keep working
      unchanged; **server** (paid) — one shared instance in the company,
      every user logs in, works on assigned projects. `mode: local|server`
      in the app config. Persistence stays JSON + one git repo per
      project (no database); the single server process allocates all ids,
      so the distributed-id problem disappears in server mode.
      (1) Authentication, pluggable providers: GitLab OAuth2/OIDC first
      (users must have a GitLab account), then generic OIDC (Keycloak,
      Entra ID), LDAP/Active Directory bind, and trusted reverse-proxy
      header (Cloudflare Access / oauth2-proxy); HTTP-only secure session
      cookie, CSRF protection, logout; every REST route and the WebSocket
      handshake require a session in server mode. (2) Users and roles:
      user registry in the server's config dir (`users.json`: id, name,
      email, provider subject, server-admin flag, active) — auto-created
      on first login; optional group → role mapping from GitLab groups /
      LDAP groups; per-project membership with roles viewer / editor /
      project-admin, enforced in `ProjectService` (one guard, REST + WS
      parity); admin pages for users and memberships; users only see
      projects they belong to. (3) Module edit locks: an editor checks out
      a module (exclusive lock: owner, since, optional reason, auto-expiry
      on inactivity, admin "break lock"); other users get read-only plus a
      "locked by X" badge; writes to a module locked by someone else are
      rejected (423); live sync shows lock changes. Optional per-
      requirement ETag still protects against stale writes by the lock
      owner's own tabs. (4) Attribution: git commits authored as the
      logged-in user (name/email from the IdP), squash window per user;
      created_by / modified_by system attributes; identity in live-sync
      events; login/logout and admin actions in an audit log (append-only
      JSON lines). (5) Editions/licensing — decided 2026-10-06: the
      repository (community/local edition) is dual-licensed AGPL-3.0-only
      + commercial (see `LICENSING.md`); the server-edition features live
      in a separate, commercially licensed package/repo (`reqman-server`)
      that plugs into the core through explicit extension points (auth
      provider, request identity, permission guard, lock service), and is
      gated by a signed licence file. The core must stay fully usable
      without it. Depends on: the
      git integration (done), distribution package (server install),
      git remotes (server can mirror project repos to GitLab).
- [ ] AGPL compliance in the app. Follow-up of the 2026-10-06 licence
      change. AGPL §13: users interacting with the app over a network must
      be offered the corresponding source — add a "Source code" link (repo
      URL + exact version/commit from the version item) in Help → About
      and the footer; licence notice in About; SPDX
      `AGPL-3.0-only` in `frontend/package.json` and (with the
      distribution item) `pyproject.toml`; a third-party licence list
      generated at build time; `CONTRIBUTING.md` with the CLA requirement.
- [ ] Context menus: follow-ups (2026-10-08). (1) Done 2026-10-08 for
      module tab entries (close, close others, detach), "Open in new
      tab" and "Detach"; done 2026-10-08 for project references (sidebar
      project menu "References…"); done 2026-10-08 for module folder
      entries (folder node menu, project node "New folder", module node
      "Move to folder…"); split view entries still arrive with that item
      (hooks in `ActionTargets` and the menu layouts).
      (2) Project/module Rename needs backend APIs. (3) Column header
      Sort / Filter / Hide / Add to view need their own features.
      (4) Detail-panel link badges and module-outline nodes have no
      left-click navigation menu. (5) ProjectPage module-card Move Up/Down
      and "Change parent" are still page-local, not registry actions.
      (6) Purge module/project is not in the sidebar menus. (7) Alt+letter
      menu mnemonics. (8) Paste as sibling / Add sibling are two server
      calls and two commits (not atomic) — consider a server-side
      copy-with-index. (9) No frontend tests (no runner) and no browser
      verification yet.
- [ ] Module tabs: follow-ups (2026-10-08). (1) Side-by-side split view
      in one window is not built; it needs per-pane module, selection and
      view state (`AppActionsProvider` and `ModulePage` take ids from the
      route). (2) Multi-selection drag: the payload is ready, bulk
      selection is missing. (3) Done 2026-10-08: cross-project drops work
      with project references (`link_options` takes `from_project_id`).
      (4) Tab icons by module type (with the module types
      item). (5) No Close-tab shortcut (the browser owns Ctrl/Cmd+W).
      (6) Not browser-verified: tabs, `window.open`/`postMessage`,
      cross-window drag-and-drop; backend tests for `link_options` are
      pending (phase B).
- [ ] AI prompt library: follow-ups (2026-10-08). (1) Render from an
      unsaved draft in the prompt editor. (2) Selection from another
      project in the UI (the backend already takes
      `selection_project_id`). (3) Link writing after the requirements
      is not atomic on an I/O error. (4) The preview's new-requirement
      table shows only ObjectText. (5) Prompt delete uses
      `window.confirm`. (6) The editor's placeholder help duplicates the
      backend's `prompts.PLACEHOLDERS`. (7) Update mode cannot clear
      values (empty cell = unchanged). (8) Multi-select of requirements
      (pairs with the bulk edit item). (9) Tests pending (phase B).
- [ ] Project lifecycle: follow-ups (2026-10-08). (1) Baseline
      references from sign-off records and impact analysis (those
      features do not exist yet). (2) Reports are always computed from
      the current data: no "compute against a baseline", and the
      traceability report has no data source. (3) For a locked project
      "References…" and "Row styles" open nothing (no read-only view).
      (4) The Purge confirm text does not mention the lock. (5) The
      remove dialog compares the name exactly while the server trims.
      (6) No browser verification, no frontend tests; backend tests
      pending (phase B).
- [ ] Side panels: follow-ups (2026-10-08). (1) Interactive behaviour is
      not browser-verified: dragging, shortcuts (including Option+Cmd+B
      on macOS and Firefox's own Ctrl+B), overlays, Escape. (2) Focus is
      not moved into or trapped in the narrow-screen overlays. (3) Any
      route change closes the narrow-screen overlay, also when picking a
      project — alternative: close only when a module opens. (4) No
      "Reset panel widths" menu action. (5) Possible gap in the navbar
      border about 90px above the footer, seen in headless screenshots
      (unconfirmed). (6) No frontend tests (no runner).
- [ ] Toolbar → menu: review follow-ups (2026-10-06). (1) Hard-delete
      safety: an open purge confirmation survives a data-folder switch
      (`root_changed`), and the captured project id is then purged in
      the new folder, where ids can repeat. Fix: close purge modals in
      the `subscribeDatasetCleared` listener, or capture `dataDir` and
      refuse a confirm when it no longer matches. 2026-10-08: the new
      Remove project dialog has this protection (it captures `dataDir`,
      the server checks `data_dir`); the purge dialogs are still
      unprotected. (2) On read-only
      baseline pages, File ▸ Export and Edit ▸ Purge project are enabled
      but act on the CURRENT data; export there silently delivers current
      content. Fix: add `baselineName` to AppState and either disable
      these actions there or label them "(current data, not baseline
      <name>)". (3) The purge mutations do not invalidate git status/log
      or other modules' link columns while live sync is disconnected; use
      the project-wide predicate invalidation. (4) The Purge project
      confirm text should mention that links are removed and show the
      module count; a typed-name confirm for project purge may be worth
      adding.
- [ ] Left panel tabs: review follow-ups (2026-10-06). (1) The Module
      outline filter is O(n²): `ancestorIdsOf` rebuilds a Map per match
      (about 1.3 s per keystroke at 5,000 requirements) — memoize, build
      the index once, consider `useDeferredValue`. (2) Keyboard tree gaps:
      the roving tabindex follows the selection, not the focus; no
      ArrowLeft-to-parent, ArrowRight-to-first-child, Home or End; the
      root drop zone sits inside `role="tree"`; a keyboard-opened context
      menu appears at 0,0. (3) The outline selection is not kept in the
      URL; the spec asks for it (e.g. `?selected=`). (4) "Expand all"
      stays enabled when stale collapsed ids remain. (5) The table
      (`ModulePage.tsx:67`, `actions/requirementActions.tsx:38`) sorts
      siblings by `order` only, without the id tie-break the outline and
      the server use — with duplicate legacy orders the two views may
      differ. (6) The `ExpandToggle` span nests interactive content inside
      the NavLink. (7) `planDrop` needs frontend unit tests once a runner
      exists (see the frontend test runner item).
- [ ] Fixed layout: review follow-ups (2026-10-07). (1) The 26rem floor
      is on the page, not on the table row: a long module or baseline
      description at small window heights can squeeze the table/detail
      row to almost 0. Fix: set min-height ~14rem on the ModulePage panel
      row and the baseline card, or line-clamp the description.
      (2) Printing (Ctrl/Cmd+P) the module page now prints only the
      visible part of the table. Fix: add an `@media print` block that
      resets the heights and overflow. (3) `scroll-padding-top: 3rem` is
      hardcoded, but header cells can wrap to 2–3 lines. Fix: nowrap the
      header cells, or derive the padding from the thead height.
      (4) `--mantine-color-body` is overridden on the whole baseline
      Card. Fix: scope it to the table scroll container. (5) Safari/
      Firefox nested flex/overflow behaviour is unverified.
- [ ] Resizable columns: review follow-ups (2026-10-07). (1) Done
      2026-10-07: the inline `position: relative` in
      `ResizableHeaderCell.tsx` overrides Mantine's per-`th` `position:
      sticky`, so the header relies on the sticky `thead` only and may
      not stick in some browsers (Safari). Fix: remove the override.
      (2) Done 2026-10-07: `document.body` cursor/user-select are not
      reset if a header cell unmounts mid-drag, so text selection stays
      disabled until reload. Fix: add an unmount cleanup. (3) Done
      2026-10-07: the last baseline column's resize handle (`right:
      -4px`) causes a 4px horizontal scroll. (4) "Save column
      widths to view" flickers to the defaults for one refetch. Fix:
      await the invalidation or use setQueryData. (5) "Save as template"
      drops view column widths (`TemplateView` has no field). (6) A
      "reset this column only" entry, and a "save widths" option in the
      view dialog. Row height resizing (added 2026-10-07): (7) The
      overflow-fade effect runs after every render and forces a layout;
      it also misses layout changes without a render (window resize,
      live column drag). Fix: ResizeObserver plus rAF, write only on
      change. (8) Every cell has one stateful `RowResizeHandle`, costly
      for big modules. Fix: stateless handles with events delegated on
      the tbody. (9) `rowHeights` entries of deleted or moved
      requirements are never pruned, and they keep "Reset row heights"
      enabled. (10) The row separator's `aria-valuenow` is missing for
      auto-height rows, and its `aria-valuemin` is wrong. (11) A
      two-line wrap cell at the minimum height shows a sliver of its
      second line without a fade. (12) Frontend unit tests for
      `parseLayout` with old entries and the reset helpers.
- [ ] Attribute applicability: follow-ups (2026-10-07). (1) Moving or
      copying a requirement to another module must drop its
      non-applicable values (via `_applicable_values`) and report them;
      removing a module must strip its id from every attribute's
      `modules` list (both were in the spec). (2) `set_attribute_modules`
      with `drop_values` rewrites the requirement files before
      `project.json`, so it is not atomic: an I/O error mid-way leaves
      values stripped while the scope is unchanged. Fix: save the
      project first, or pre-check writability. (3) WS `attribute.add`
      does not type-check `required` (REST coerces it via pydantic), so
      `"required": "false"` is truthy over WS. Fix: validate that it is
      a bool in the service. (4) `docs/ARCHITECTURE.md`: the
      required-scope paragraph splits the "Format versions" intro from
      its bullets; move it. (5) A required attribute with a hand-edited
      scope cannot be reset from the UI (the Modules button is
      disabled); only the API can clear it.
- [ ] Row styling: review follow-ups (2026-10-08). (1) Data safety, do
      first: a missing `row_styles` key in the REST body or WS params
      clears every rule. Fix: make the key required; clearing needs an
      explicit null or []. (2) Do first: `test_attribute_applicability.py:66`
      had its format assertion deleted rather than updated; restore it as
      `== PROJECT_FORMAT.current`. (3) `RowStylesModal` seeds from the
      project captured when the dialog opened: reopening before the
      refetch shows old rules, and concurrent edits are overwritten. Fix:
      read the live `useProject`. (4) Dark mode: background-only rules do
      not check the contrast of the default text (e.g. `#fff3bf` gives
      about 3:1). Fix it, or correct the 4.5:1 claim in
      `docs/ARCHITECTURE.md`. (5) On selected/hovered rows the rule's font
      color sits on the selection tint with low contrast. Fix: drop the
      color while the row is selected. (6) The Id cell's inline `fw=600`
      beats the bold rule. (7) Precompute the per-rule appearance instead
      of doing it per row. (8) `set_row_styles` saves (and commits) even
      when nothing changed. (9) Frontend unit tests for `rowStyles.ts`
      once a test runner exists.
- [ ] Rich text slice 1: review follow-ups (2026-10-08). (1) CSV import
      stores `from_plain` output without a size check. A crafted cell
      starting with the wrapper can exceed the limits, so later
      reads/exports silently drop the tail and copy fails. Report it as a
      row error instead. (2) Parser parity gaps with the browser: no
      adoption agency (`<b><p>x</b>y</p>` gives an extra empty p), table
      insertion modes, VOID self-closing `<br/>`/`<hr/>` without the
      implied p-close. (3) `EditableAttributeCell`: a live-sync update
      while editing resets `draft`, so the user's unchanged-since-sync
      edit can be lost silently. Track a dirty flag. (4) Python
      `\d`/`strip()` vs JS differences (Unicode digits in `rgb()`,
      `\x85`); use ASCII classes. (5) The lazy editor chunk ignores
      outside click/Esc while loading; preload it on hover. (6) Font
      sizes duplicated in `richtext.py` and `richText.ts`. Add a shared
      fixture file checked by both sides once a frontend test runner
      exists. (7) A whitespace-only value becomes "" after a real edit;
      `\r\n` becomes `\n` on edit.
- [ ] Module templates: follow-ups (2026-10-08). (1) No import or delete
      of module template files (pairs with "User-template management
      dialog"). (2) No "make active view" option after adding a module
      from a template. (3) `GET /templates` does not show which module
      templates and skeletons a project template uses. (4) No buttons on
      ProjectPage/ProjectTab (menus only). (5) Module templates cannot
      carry `module_types` scopes or required attributes. (6) A skeleton
      cannot be applied to an existing module. (7) The result summary
      shows only a count of extended attributes. (8) 9 existing template
      tests expect format version 1 and need updating; new tests are
      pending (phase B).
- [ ] Module types and reports: follow-ups (2026-10-08). (1) Tests for
      all three slices (phase B); existing tests that assert project
      format 4, the old module type list or the old RBC module/attribute
      lists need updating. (2) No transitive verification (e.g. StRS
      verified via SyRS → SysTest). (3) No grid between non-adjacent
      modules of a chain. (4) No cross-project chains (waits for "Project
      references"). (5) The module tab menu has no report entries.
      (6) Result import: no heading per run; no option to import
      unmatched results as unlinked records; other formats (xUnit/NUnit,
      TestNG, Cucumber JSON). (7) No sort/filter by Verification status.
      (8) "Verification status column" is shown disabled instead of
      hidden in the header menus of test modules. (9) The module type
      cannot be changed after creation. (10) `RBC_Template` keeps the old
      "Test Result" on test cases next to the execution "Result"
      (duplication; consider removing it).
- [ ] Project references: follow-ups (2026-10-08). (1) Cross-project
      traceability chains in the matrix (currently 400) and cross-project
      coverage in the verification and test execution reports. (2) ReqIF
      SPEC-RELATIONs for cross-project links (e.g. as external
      references); today only the mirror attribute is written. (3)
      Incoming cross-project links as a table column; badges show project
      ids, not project names. (4) The purge confirmation does not list
      the referencing projects whose link pairs will be deleted. (5)
      Baseline view/compare do not resolve cross-project link ends. (6)
      Tests pending (phase B).
- [ ] Module folders: follow-ups (2026-10-08). (1) Drag-and-drop only
      moves a module or folder into a folder (or onto the project name);
      there is no drop-between-rows reordering. (2) The `GET /templates`
      summary does not expose a template's folders. (3) The sidebar
      collapse state (`reqman.moduleFolders.collapsed`) is keyed by
      project and folder id but not by data folder. (4) Grouped module
      pickers list unfoldered modules first, then one group per folder
      path, so they are not strictly in tree order. (5) About 7 existing
      tests assert the old flat `module_order` and need updating; new
      tests are pending (phase B).
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
      projects (no such routes today); rename views (no route today; the
      project's case-insensitive unique view-name check is ready for it);
      a `multi_enum` type. Pairs with the
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
- [ ] Unloadable project as a parent: open decision. Follow-up of the
      project-list stub item (2026-10-04). The server still accepts a
      stub whose `project.json` is invalid (not newer) as the parent when
      creating or moving a project (only a newer parent is rejected),
      while the UI parent pickers disable every stub. Decide: should the
      server refuse any unloadable parent (REST 400 / WebSocket -32602)?
- [ ] Over-long link configuration and template names. Follow-up of the
      project-list stub item (2026-10-04). Project and module names are
      checked against the filesystem name limit, these are not: a link
      configuration name gives a 500 with a partial write (`project.json`
      is saved before the link-pair file fails); a user template name
      (save as template, template import) gives a 500; a template module
      name gives a 500 while staging (nothing published). Reject them
      with REST 400 / WebSocket -32602 and write nothing.
- [ ] User-template management dialog. Follow-up of the menu bar item
      (2026-10-04). List the user templates in `_templates/` (with invalid
      ones and their error) and delete them; today they can only be added
      (Save as template, Load template ▸ import template file).
- [ ] Frontend test runner. Follow-up of the menu bar item (2026-10-04).
      The frontend has no test runner, so the action registry and shortcut
      logic (`frontend/src/actions/`), like earlier frontend work, is
      untested; add Vitest + Testing Library and cover the registry
      availability rules and the shortcut focus guards.
- [ ] Sort/filter the requirements table by attribute value, plus a simple
      full-text search box (module or project scope). Table-stakes for any
      module with more than a screenful of requirements. Not yet requested.
- [ ] Suspect links: flag a link when either linked requirement's content
      changed after the link was made (e.g. compare a stored
      last-linked-at attribute value/hash against the current one), and
      surface it visually (e.g. a warning color on the `>`/`<` badge). Not
      yet requested — DOORS' core value-add over "just having links."
- [ ] Comments/discussion thread per requirement. Not yet requested.
- [ ] Multi-select + bulk attribute edit across several requirements at
      once. Not yet requested.
- [ ] `user` (who) as an additional attribute type. The `date` type is
      done (see Done, 2026-10-08). Not yet requested.
- [ ] Printable/publishable document view of a module (formatted export to
      a readable spec document, distinct from the structured ReqIF/CSV/
      Excel/Word exports above), generalized into custom report templates
      (merged feature-scout candidate, 2026-10-04): a user template picks
      modules, attributes, filters and layout, so the spec document, the
      traceability/verification reports and project-specific deliverables
      share one mechanism. Not yet requested.
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
