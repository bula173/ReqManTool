# ReqManTool product backlog

Delivered items are recorded in [DONE.md](DONE.md); this file lists only
what is still open.

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

**Administration** — user accounts / permissions (who can edit vs. view) ✅
in the server edition (login, users, project membership roles; the local
edition stays single-user), concurrent-edit protection (two people editing the same requirement at once
— server edition: module edit locks; per-requirement ETag still open) ✅, role sign-off
records with an independence check (EN 50128 roles) ❌, tool qualification
evidence (EN 50128 §6.7) ❌.

**Reporting / publishing** — printable/publishable formatted document view
of a module (the classic "generate a spec document" DOORS output) ❌,
custom report templates ❌.

## Backlog (not started)

Updated 2026-10-08. Mode chosen by the user: implement every requested
item first (phase A), then test, review and fix by area (phase B).

### Next — requested, in delivery order

- [ ] Server edition: access from GitLab/GitHub repository permissions.
      Requested 2026-10-08, PRIORITY. Decision: variant 2 — the role of a
      user in a project is derived from their permission on that
      project's git repository (one source of truth; revoking access in
      GitLab/GitHub revokes it in ReqManTool). Mapping: GitLab Guest /
      Reporter, GitHub Read → viewer; GitHub Triage → reviewer; GitLab
      Developer, GitHub Write → editor; GitLab Maintainer / Owner, GitHub
      Maintain / Admin → project-admin. Resolved at login and refreshed
      periodically / on demand (admin "Sync access now"), cached with a
      short TTL; projects without a remote, or whose provider is not the
      login provider, fall back to the existing in-app membership (admin
      UI); server-admin stays in-app. New role **reviewer**: read plus
      comment and status sign-off, no content edits. Status guards in
      `ProjectService`: Draft → Reviewed only by a reviewer (or higher)
      who is not the requirement's last author (independence), Reviewed →
      Approved only by project-admin (later: an approver role);
      project-admin only for baselines, lock/unlock, attributes, views,
      link configurations. Server-only code in `backend/reqman_server/`
      (provider permission lookup via the GitLab/GitHub API with the
      user's OAuth token); the local edition is unaffected. REST + WS
      parity; errors name the missing role and its source ("needs
      Developer on gitlab.example.com/rbc/rbc-gp").
- [ ] Server edition: more identity providers and group-based roles.
      Requested 2026-10-08 (after the repository-permission item). Login
      providers, all pluggable through the existing server auth extension
      point and configurable side by side: (1) LDAP / Active Directory —
      bind with the user's credentials (LDAPS/StartTLS only), user and
      group lookup, nested AD groups; (2) generic OIDC presets — Keycloak,
      Microsoft Entra ID (Azure AD), Okta, Google Workspace, Authentik —
      using the `groups` claim; (3) SAML 2.0 SP for corporate IdPs (ADFS,
      Entra, Okta); (4) trusted reverse-proxy header (Cloudflare Access,
      oauth2-proxy) with signature/JWT verification; (5) optional SCIM 2.0
      endpoint so the IdP provisions/deprovisions users and groups.
      Authorisation for projects not governed by repository permissions:
      admin-defined mappings "IdP group → role per project" (or all
      projects), evaluated with the same roles (viewer / reviewer /
      editor / project-admin); repository permissions keep precedence
      where they apply. MFA and password policy stay with the IdP;
      ReqManTool never stores passwords. Admin UI to configure providers
      and mappings, with "Test connection"; secrets only in the server
      config/env, never in project data. Server-only code in
      `backend/reqman_server/`.

- [ ] Desktop app for the local edition (no installation hassle).
      Requested 2026-10-08. One-click installers built by GitHub Actions
      on each release: macOS `.dmg` (signed + notarised when a certificate
      is available), Windows `.msi`, Linux AppImage. Bundles the backend
      (frozen Python, no system Python/npm needed) and the built frontend
      in a native window (Tauri preferred for size; Electron acceptable),
      starts the local server on a free localhost port, stops it on exit;
      first run asks for the data folder (default `~/ReqManData`);
      auto-update from GitHub Releases; git integration uses the system
      `git` if present, otherwise history is off with the usual warning.
      Builds on the distribution package (done) — the pipx install stays.
- [ ] Docker image for the server edition (self-hosted in a company).
      Requested 2026-10-08. Official image (multi-arch amd64/arm64,
      non-root, healthcheck) published by GitHub Actions; `docker compose`
      example with a persistent volume for the data directory, config via
      env/secret files (OIDC/LDAP/GitLab settings, licence file), optional
      reverse proxy with TLS; Helm chart for Kubernetes; one-click deploy
      templates where practical (Railway, Render, Azure Container Apps);
      upgrade notes incl. data-format migrations; backup/restore of the
      data volume documented (project repos can also be mirrored to
      GitLab via remotes). Depends on the server-edition items.

### Phase B — tests, review and follow-ups of delivered items

Collected review findings and leftovers of the items in Done; handled
area by area once phase A is complete.

- [ ] Git integration follow-ups from slice 2a (2026-10-04): git runs
      synchronously on the event loop (about 11–12 git processes,
      ~80–200 ms per mutation; CSV import of 3000 requirements 17 s →
      20 s) and sessions have no maximum length — decide on a session
      cap and/or moving git work off the event loop; `git merge
      --squash` (SQUASH_MSG) does not pause history; separate git dirs
      and linked worktrees are refused (`foreign`), supporting them is
      open; Windows: with no private hooks dir it falls back to
      `/dev/null`, which resolves under the drive root (report an error
      instead?), and `os.path.isjunction` needs Python 3.12+ (the
      `requires-python` pin is now `>=3.11` in `backend/pyproject.toml`,
      so 3.11 is still allowed); a settings UI and config keys for the git
      settings (needs a CONFIG_FORMAT bump with migration); raw
      `git blame` fallback; author from Cloudflare Access once user
      handling exists (remotes/push: done, see "Git remotes" in Done).
      The user's current data dir inside the ReqManTool checkout
      shows `nested`, so it gets no history until moved.
- [ ] Git baselines: review follow-ups (2026-10-05, from the slice 2b
      review). Browse reads the whole baseline tree into memory per
      request — scope `ls-tree`/`cat-file` to the requested module;
      hand-made nested tags like `baseline/x/y` are listed but not
      browsable (filter or mark them); a committed data file with a
      newline in its name vanishes from 2a's pending-changes specs
      (`cat-file --batch` line protocol); a hand commit whose subject contains
      `\x1f`/`\x1e` corrupts that git-log entry (handled in the commit
      browser; the existing git-log entry path unchanged); `moduleId`
      is not URL-encoded in the baseline
      requirements query; mutations refetch active immutable baseline
      snapshots despite `staleTime: Infinity`; a description whose final
      paragraph looks like `Reqman-*:` trailers is swallowed when
      listing.
- [ ] Git remotes: review follow-ups (2026-10-06), all nits. (1)
      `GitStatusPanel` RemoteSync keeps its `editing`/`url` local state
      when live sync changes the remote while the form is open — reset on
      a `remote.url` change (cosmetic). (2) Auto-push runs synchronously
      inside the mutation (10 s timeout) — consider pushing in the
      background so an unreachable remote never stalls edits.
      (3) `_is_non_fast_forward` matches "[rejected]" but not "[remote
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
- [ ] GitLab/GitHub: follow-ups (2026-10-08). (1) Part (4) of the
      original item: link requirements to issues (issue URL attribute,
      create issue from a requirement). (2) Server-edition OAuth login
      for provider connections (with the server edition item; hook point
      `TokenStore` / `ProviderService.token`). (3) Review target branch
      other than `current` and a draft option. (4) Namespace paging
      beyond 100. (5) GitHub repository search is limited to the 500
      most recently updated repositories. (6) The macOS keychain may
      prompt on the first listing. (7) Tests pending (phase B); one
      existing assertion in `tests/test_git_remotes.py` was updated for
      the new `review` key.
- [ ] Context menus: follow-ups (2026-10-08). (1) Split view
      entries still arrive with that item (hooks in `ActionTargets` and
      the menu layouts).
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
      selection is missing. (3) Tab icons by module type (with the module
      types item). (4) No Close-tab shortcut (the browser owns
      Ctrl/Cmd+W). (5) Not browser-verified: tabs, `window.open`/`postMessage`,
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
- [ ] Resizable columns: review follow-ups (2026-10-07). (1) "Save column
      widths to view" flickers to the defaults for one refetch. Fix:
      await the invalidation or use setQueryData. (2) "Save as template"
      drops view column widths (`TemplateView` has no field). (3) A
      "reset this column only" entry, and a "save widths" option in the
      view dialog. Row height resizing (added 2026-10-07): (4) The
      overflow-fade effect runs after every render and forces a layout;
      it also misses layout changes without a render (window resize,
      live column drag). Fix: ResizeObserver plus rAF, write only on
      change. (5) Every cell has one stateful `RowResizeHandle`, costly
      for big modules. Fix: stateless handles with events delegated on
      the tbody. (6) `rowHeights` entries of deleted or moved
      requirements are never pruned, and they keep "Reset row heights"
      enabled. (7) The row separator's `aria-valuenow` is missing for
      auto-height rows, and its `aria-valuemin` is wrong. (8) A
      two-line wrap cell at the minimum height shows a sliver of its
      second line without a fade. (9) Frontend unit tests for
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
- [ ] Distribution package: follow-ups (2026-10-08). (1) Tests pending
      (phase B): config precedence and lookup, `save_data_dir` key
      preservation, SPA fallback and its non-shadowing of API/`/ws`/
      downloads, the CLI, systemd/launchd unit generation. (2) The CI
      and release workflows have never run on GitHub. (3) Windows has no
      `reqman service` support. (4) The sdist build warns that there is no
      README. (5) `/config` does not report which config file is in use.
      (6) No migration command from the checkout's `.reqman/config.json`
      to the per-user file. (7) The OS keychain (`keyring`) under a
      systemd user unit without a secret service is not handled. (8) An
      older version's data-folder switcher would drop `host`, `port` and
      `cors_origins` when saving the config file.
- [ ] Two-step linking: follow-ups (2026-10-08). (1) If the source
      requirement is deleted while pending, the bar stays and "Make link"
      gets the server error (the user must cancel by hand). (2) Not
      verified in a browser: cross-window sync, the detached-window bar,
      the menus. (3) On narrow screens one Esc closes a side-panel overlay
      and cancels the pending link at once. (4) No frontend tests (phase
      B).
- [ ] AGPL compliance: follow-ups (2026-10-08). (1) The actual CLA text
      or a CLA-assistant setup must be supplied by the user
      (`CONTRIBUTING.md` only states the requirement). (2) A fork's
      frontend falls back to the upstream URL when the backend is
      unreachable (a build-time `define` override is possible). (3) Commit
      links 404 for unpushed local commits (only uncommitted changes are
      detected). (4) No Help-menu item opens the third-party licence list
      directly. (5) `licensesOpen` stays true if About is closed from
      outside. (6) Environment markers other than `extra` are not
      evaluated in the Python third-party list. (7) Tests pending (phase
      B); `tests/test_version.py::test_app_startup_prewarms_git_build`
      was adjusted.
- [ ] Server edition: follow-ups (2026-10-08). (1) LDAP/Active Directory
      bind and trusted reverse-proxy header providers (Cloudflare Access /
      oauth2-proxy): only the provider type is validated today. (2) Signed
      licence-file gate. (3) `LICENSING.md` says the server edition is not
      in this repository, but `backend/reqman_server/` now is: reword
      `LICENSING.md` or move the package (user decision). (4) Optional
      JWKS signature verification of the ID token. (5) Close the user's
      other open WebSockets immediately at logout. (6) Logged-out page
      loads of non-client routes get a 401 JSON response. (7) Per-user
      provider connections and a remote-host allowlist for non-admins.
      (8) User picker for project admins. (9) Memberships and locks are
      keyed by project id and not scoped per data folder. (10) An open
      project shows an error after access is lost instead of closing its
      tab. (11) No push event when a module lock expires (1-minute
      refetch). (12) Lock badges only for the open project. (13) Per-
      requirement ETag/If-Match against stale writes by the lock owner's
      own tabs. (14) `git_pull` and remove-project interplay with module
      locks. (15) Audit log rotation/size cap; failed-login rate limiting.
      (16) The `by` identity in `changed` events is not shown in the UI.
      (17) The Audit log tab lacks user/project filters. (18) Project
      admins cannot see their projects' audit entries. (19) Lock releases
      at logout/deactivation are not audited. (20) `reqman init` writes
      no `server` section template. (21) Tests pending for all four
      slices (phase B; fake-IdP end-to-end scripts are in the session
      scratchpad).

### Open decisions

- [ ] Unloadable project as a parent: open decision. Follow-up of the
      project-list stub item (2026-10-04). The server still accepts a
      stub whose `project.json` is invalid (not newer) as the parent when
      creating or moving a project (only a newer parent is rejected),
      while the UI parent pickers disable every stub. Decide: should the
      server refuse any unloadable parent (REST 400 / WebSocket -32602)?

### Shelved

- [ ] Frontend test runner. Follow-up of the menu bar item (2026-10-04).
      The frontend has no test runner, so the action registry and shortcut
      logic (`frontend/src/actions/`), like earlier frontend work, is
      untested; add Vitest + Testing Library and cover the registry
      availability rules and the shortcut focus guards.
      Shelved 2026-10-08 by the user ("bez testów"): the Vitest setup and
      154 tests are kept in the git stash "shelved: frontend test runner
      (Vitest)"; not part of phase B.

### Ideas — not requested yet

Feature-scout suggestions and older ideas, ordered roughly by value.

- [ ] Hosted cloud edition (SaaS) — distant option, noted 2026-10-08.
      Multi-tenant ReqManTool run by the vendor: sign-up and login via
      GitLab/GitHub/corporate SSO, no installation; projects preferably
      stay in the customer's own GitLab/GitHub repositories (the service
      works on clones), tenant isolation, backups, monitoring, billing,
      GDPR/data-processing agreement and possibly ISO 27001. Only once
      customers ask for it — rail customers often require self-hosting.
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
- [ ] Import ReqIF (the CSV slice is in docs/DONE.md). Reuse the CSV
      import's all-or-nothing validation (collect every error, write
      nothing on failure) and its server-allocated-id approach (the file's
      identifiers are only keys for resolving parents within the file).
      Open issue carried over from CSV: an attribute named Id/Module/Parent,
      or two attributes sharing a name, breaks re-import of a project's own
      CSV export — product decision needed: reject such names in
      `add_attribute_definition`, or match those columns by attribute id.
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
