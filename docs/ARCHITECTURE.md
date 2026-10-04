# ReqManTool architecture

This document describes how ReqManTool is built, so a new contributor can
find their way around without archaeology. For what's built vs. planned, see
[`BACKLOG.md`](BACKLOG.md). For the user-facing pitch, see the root
[`README.md`](../README.md).

## Goals and non-goals

ReqManTool is a small, local-first, DOORS-inspired requirements management
tool aimed at teams too small to justify an enterprise RM tool, but who
still want real structure: modules, hierarchical requirements, custom
attributes, directional traceability links, and ReqIF/CSV export.

Deliberate constraints that shape every design decision below:

- **Local-first, file-based storage.** Every project is a folder of JSON
  files on disk, not a database. This is a *feature*: history, backup,
  branching, and code review of requirements use ordinary git, the same as
  source code. Each project folder is its own git repository that
  ReqManTool commits to automatically (see "Project history (git)" below). The backend never assumes a
  network filesystem, a database server, or multiple concurrent writers.
- **Single active user per data directory.** There is no authentication and
  no locking. Two people editing the same requirement at once is
  last-write-wins. This is a known, accepted limitation (see `BACKLOG.md`),
  not an oversight — fixing it would mean adding real server-side state and
  contradicts "your data directory is just files you own."
- **No required external services.** SQLite/Postgres, message queues,
  object storage — none of it. The whole stack is a Python process and a
  static frontend bundle; `etc/start.sh` is the entire deployment story.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python, FastAPI, uvicorn | async REST + WebSocket in one process, good typing, small dependency footprint |
| Storage | Plain JSON files via `pathlib`/stdlib `json` | see "local-first" above; no ORM, no migrations |
| ReqIF export | [`reqif`](https://github.com/strictdoc-project/reqif) (PyPI) | correctness for a nontrivial XML dialect is not worth hand-rolling |
| Frontend | React + TypeScript + Vite | standard, fast dev loop, small footprint for a single-user local app |
| UI components | Mantine (`@mantine/core`, `/form`, `/modals`, `/notifications`) | batteries-included widgets (tables, modals, forms) without a large design-system investment |
| Server state | TanStack Query | request caching/invalidation, avoids hand-rolled loading/error state per screen |
| Live sync | Raw WebSocket + a JSON-RPC-shaped message protocol | reuses the same connection the RPC transport already needs; no separate SSE/pub-sub layer |

## System shape

```
┌─────────────────────┐        REST (JSON over HTTP)        ┌───────────────────────────┐
│                      │ ───────────────────────────────────▶│                           │
│   Frontend (Vite)    │                                      │   Backend (FastAPI)       │
│   localhost:5173     │◀───────────────────────────────────  │   localhost:8899          │
│                      │        WebSocket (/ws)               │                           │
└─────────────────────┘   - JSON-RPC 2.0 requests/responses   └────────────┬──────────────┘
                           - unsolicited "changed"/"root_changed"          │
                             broadcasts (live sync)                        │ reads/writes
                                                                            ▼
                                                                 ┌────────────────────┐
                                                                 │  Data directory     │
                                                                 │  (plain JSON files, │
                                                                 │  see layout below)  │
                                                                 └────────────────────┘
```

Every domain operation (create a project, add a requirement, link two
requirements, …) is reachable **two ways** — a REST endpoint and a
WebSocket JSON-RPC method — and both call the exact same
`ProjectService` method, so behavior can never drift between the two
transports (see "Backend layering" below). The frontend uses REST for all
data fetching/mutation (`frontend/src/api/queries.ts`, via TanStack Query)
and keeps one WebSocket connection open app-wide purely for **live sync**:
after any mutation, the backend broadcasts `{"event": "changed",
"project_id": ...}` to every *other* connected client, which invalidates
the relevant queries and refetches. Switching the data directory broadcasts
`{"event": "root_changed"}` instead, which clears the entire client cache
(the whole project/module/requirement set is now a different dataset).
Saving a project as a template broadcasts `{"event": "templates_changed"}`,
which refetches the template list.

## Data model and disk layout

All persisted types are plain Python `@dataclass`es in
`backend/reqman/models.py`, each with `to_dict()`/`from_dict()` for JSON
(de)serialization. Field order in the dataclass is the JSON key order on
disk (via `dataclasses.asdict`), kept stable on purpose — see "Git-friendly
persistence" below.

```
<data-directory>/
  _templates/                     # user project templates (never a project)
    <template-id>.json            #   see "Project templates" below
  .reqman-staging/                # transient: a project created from a
                                  #   template is assembled here, then
                                  #   renamed into place (removed afterwards)
  <project-id>/                   # one git repository per project (subprojects
                                  #   are sibling folders, never nested)
    .git/                         # created by ReqManTool (see "Project history")
    project.json                  # format_version (first key), then
                                   #   Project: id, name, description,
                                   #   parent_project_id (only if set),
                                   #   attributes (incl. system ones),
                                   #   id_counters, link_configurations, views,
                                   #   module_order (only if set)
    modules/
      <module-id>/
        module.json                # Module: id, name, prefix, description
        requirements/
          <requirement-id>.json    # Requirement: id, attributes,
                                    #   parent_id, order
    links/
      <link-config-id>.json        # {"pairs": [{"source_id", "target_id"}, ...]}
                                    # the "link module" — source of truth
                                    # for one link configuration's pairs
```

- **Project** (`project.json`) — `id` (slug, see below), `name`,
  `description`, `parent_project_id: str | None` (see "Subprojects" below;
  the key is written right after `description` and **only when set**, so a
  top-level project's file keeps exactly the keys it had before subprojects
  existed and old files are not rewritten with a new `null` line),
  `attributes: list[AttributeDefinition]` (applies to every
  module in the project — there is no per-module attribute schema),
  `id_counters: dict[prefix, int]` (requirement id allocation state, one
  counter per module *prefix*, shared across modules that use the same
  prefix), `link_configurations: list[LinkConfiguration]`,
  `views: list[View]` (view names are unique per project, compared
  case-insensitively after stripping, like template view names),
  `module_order: list[str]` (module ids in display order; written last and
  only when non-empty — see "Module order" below).
- **AttributeDefinition** — `id`, `name`, `type` (`string | text | integer
  | boolean | enum`), `required`, `default`, `choices` (enum only),
  `system: bool`. `system=True` marks an attribute the service manages
  automatically — currently only the read-only mirrors that link
  configurations create (see "Link configurations" below);
  `ProjectService._validate_attributes` rejects any attempt to set a
  system attribute directly through `add_requirement`/`update_requirement`.
  `name` heads the attribute's column in the CSV/XLSX exports and is how the
  CSV import matches columns, so `add_attribute_definition` stores it
  stripped and rejects (400) an empty name, `Id`/`Module`/`Parent` (the
  reserved export columns), or a name another attribute of the project
  already has — all compared case-insensitively. `create_link_configuration`
  applies the same rule to the link name (its mirror attribute heads an
  export column too), with errors starting "Link name". Existing projects
  that already have such names are not rewritten; their CSV import treats
  `Id`/`Module`/`Parent` as the reserved columns and needs the attribute's
  column headed with its `id`.
  `add_attribute_definition` also rejects (400) a `type` outside
  `string | text | integer | boolean | enum` (checked first; "Unknown
  attribute type '<type>'; expected one of …"), an `enum` without a
  non-empty list of string `choices`, `choices` on any other type (they must
  be `null`), a `default` that is not a valid value for the type (same
  check as requirement values below; `null` means no default), and a blank
  default (`""` or whitespace only) on a `required` attribute ("Attribute
  '<Name>' is required, so its default must not be empty"), which could
  never satisfy the required check. Stored legacy definitions with an
  unknown type are not rewritten; their values are not type-checked.
- **Attribute values** — `ProjectService._attribute_problems` is the single
  validation path for requirement attributes, used by REST and WebSocket
  add/update and by the CSV import (per row, after cells are parsed into
  typed values). Besides unknown/system attributes, it checks value types:
  `integer` must be a JSON integer (not a boolean or float, not a numeric
  string), `boolean` a JSON boolean, `enum` one of `choices`, `string`/`text`
  a string. `null` (unset) is accepted for every type except as the value of
  a required attribute; violations are 400 errors. Required attributes must
  not be blank (`null`, `""` or whitespace only — "required attribute
  '<Name>' is empty"): on create the check runs on the merged result, so an
  omitted attribute may be filled by its default, but an explicit blank
  value overrides the default and is rejected; on update only the
  attributes being written are checked. Defaults are merged into a new
  requirement by `_merged_attributes`, which never applies defaults of
  system attributes and skips a stored default that is invalid for its type
  (legacy data; the stored definition is not rewritten).
- **Module** (`module.json`) — `id`, `name`, `prefix` (e.g. `"REQ"` — the
  namespace requirement ids in this module are allocated from), `description`.
- **Requirement** (`<id>.json`) — `id`, `attributes: dict[str, Any]` (keyed
  by `AttributeDefinition.id`), `parent_id: str | None` (another
  requirement in the *same module*, forming a tree — see "Nesting" below),
  `order: int` (position among siblings sharing `parent_id`).
- **LinkConfiguration** — `id`, `name`, `source_module_id`,
  `target_module_id`. Defines one *directional*, named link type between
  two specific modules — see "Link configurations" below.
- **LinkPair** — `source_id`, `target_id`. One row per actual link,
  persisted per configuration in `links/<config-id>.json`.

### Id generation

Nothing in ReqManTool asks the user to type an id:

- **Project/module ids** are slugs derived from the name
  (`_unique_slug` in `service.py`): lowercase, non-alphanumeric runs
  collapsed to `-`, and a `-2`, `-3`, … suffix appended on collision.
  Ids are never truncated: a name whose id (with any suffix) would be
  longer than the file system allows for a folder name is rejected
  (`ValueError`: REST 400, WebSocket -32602, nothing written), and the
  collision search gives up with the same error after 10 000 candidates.
- **Requirement ids** are `<module.prefix>-<sequence>`, zero-padded to 4
  digits (`REQ-0001`). The sequence is tracked in
  `Project.id_counters[prefix]` — **per prefix, project-wide**, not per
  module — so two modules sharing a prefix continue one sequence and never
  collide. This is what makes requirement ids safe to use as the sole
  cross-module reference (links, `parent_id`) without a module qualifier.
  Counters only ever increase — removed/purged ids are never reused.

### Nesting

Any requirement can have children (`parent_id` pointing at another
requirement) — this is how "chapters" work: a chapter is just a plain
requirement that happens to have children, not a distinct node type.
Nesting is **scoped to one module**; `parent_id` must reference a
requirement in the same module (enforced in
`ProjectService.add_requirement`/`move_requirement`). Reparenting
(`move_requirement`) rejects self-parenting and moving under one's own
descendant (`_descendant_ids` walks the parent chain to detect the cycle).
Removing a requirement with children **promotes** them one level (their
`parent_id` becomes the removed requirement's own `parent_id`) rather than
cascading the delete or leaving orphans. Siblings carry an explicit `order`
(see `reorder_requirement`, `move_requirement`, `copy_requirement` — all
three keep it consistent: append-at-end on move/copy, adjacent swap on
reorder).

### Link configurations ("link modules")

Free-form linking (any type string, any target, no direction) was replaced
by **DOORS-style link configurations**: a project-level `LinkConfiguration`
names a link type and pins it to an ordered pair of modules
(`source_module_id` → `target_module_id`). Once a project has any
configuration, creating a link always goes through one —
`ProjectService.link_requirements` checks that the source/target
requirements' actual modules match the configuration exactly (this is what
enforces direction).

Each configuration's pairs are the **source of truth**, stored centrally at
`links/<config-id>.json` (the "link module") — not on the requirement
files. On every link/unlink, `_refresh_link_attribute` recomputes a
**read-only mirror** on each side: the linked requirement gets an
auto-generated `system=True` attribute (id = the configuration's id) whose
value is a `"; "`-joined list of `"> target_id"` (outgoing) / `"< source_id"`
(incoming) entries. Modeling the mirror *as a normal project attribute*
(rather than inventing a parallel "link columns" concept) means the
existing per-attribute table column and ReqIF-attribute-export machinery
picks it up for free — see `frontend/src/components/EditableAttributeCell.tsx`'s
`attr.system` branch (renders read-only, parses the mirror string into
colored `>`/`<` badges) and `reqif_export.py` (every project attribute,
system or not, becomes an exported `SPEC-OBJECT` value; link configurations
*additionally* become `SPEC-RELATION-TYPE`/`SPEC-RELATION`s from the
central pairs store).

### Subprojects

`Project.parent_project_id` forms a generic → specific product-line tree
in the EN 50129 sense (e.g. RTE → RBC_GP → RBC_GA_NCBIR → RBC_SA_NCBIR →
RBC_NCBIR); `None` means top level. It is a pure grouping: link
configurations, attributes and requirements stay within one project.
`ProjectService._check_parent` enforces the rules for `create_project` and
`set_project_parent` (`PUT /projects/{id}/parent`): the parent must be the
id of an existing project as listed in the data directory (404 otherwise;
a path such as `a/../b` or an absolute path is never a project id), not the
project itself, and not one of its descendants (400 — the only way to form
a cycle is reparenting). `ProjectStore` treats any project id that is not a
single plain path component as not found. Data written by hand can still
hold a dangling or cyclic parent: the ancestor walk only follows listed
project ids and stops at a missing, unreadable or already-visited project,
but a parent (or, when reparenting, any ancestor) written by a newer
ReqManTool is refused with `NewerFormatError` (REST 409, WebSocket -32602,
nothing written) for both "create under" and "move under", since its own
parent cannot be read to rule out a cycle; `list_projects` reports such projects as top level (in the response
only; the stored value is kept, and `GET /projects/{id}` returns it as
stored), so the sidebar tree always terminates. Reparenting such a project
(dangling or in a cycle) to `null` repairs it; the "Change parent project"
dialog compares the choice with the stored parent from `GET /projects/{id}`,
so choosing "None (top level)" is possible even though the list already
shows the project as top level.
There is no project-removal operation (purge only deletes requirements), so
the app itself never leaves a dangling parent.

### Project templates

A template is a JSON file holding a project's *structure* — attribute
definitions, modules, link configurations between those modules, and views
— and no requirements. `backend/reqman/templates.py` documents the format
(`format_version` 1) and checks its shape; `ProjectService` applies the
domain rules.

- **Ids are namespaced by location**, so templates can never collide or
  shadow each other: `builtin:<stem>` for the read-only files shipped in
  `backend/reqman/templates/` (currently `builtin:rbc_template`, the
  ERTMS/ETCS RBC structure for EN 50126/50128/50129) and `user:<stem>` for
  `<data-directory>/_templates/<stem>.json`. The file stem is the identity;
  the `id` key inside the file is informational. Lookups pick from the
  listed files, never by joining a raw id into a path.
- **`_templates/` is never a project**: project ids are slugs
  (`[a-z0-9-]`), which can never be `_templates` or `.reqman-staging`, and
  `ProjectStore` skips both names when listing, checking or loading
  projects, even if a `project.json` is put there by hand.
- **Validation is the create path**: `_build_from_template` builds the
  project in memory with the same helpers as `add_attribute_definition`,
  `create_module`, `create_link_configuration` and `create_view`
  (attribute name/type/choices/default rules, prefix rule, link-name rule,
  view references), so a template can never produce a project those
  operations would reject. `templates.py` adds template-only checks:
  required keys and types, unique module keys/names, link keys and view
  names, link `source_module`/`target_module` must be module keys, and view
  entries must be `object_text`, a template attribute id, or `link:<key>`
  (the mirror attribute of that link configuration). Duplicate module
  prefixes are allowed, as in projects (they share one id counter).
- **Compatibility**: unknown keys are ignored at every level, so a later
  optional key (for example a module `type`) needs no `format_version`
  bump. `format_version` is bumped only for incompatible changes; a
  template with any other `format_version` than 1 is invalid.
- **Listing** (`GET /templates`): built-in templates, then user templates,
  each sorted by name, each fully validated. An invalid file (unreadable,
  bad or too deeply nested JSON (over 100 levels, `MAX_TEMPLATE_JSON_DEPTH`), a string that is not valid UTF-8 such as a
  lone `\ud800` escape, wrong shape, unsupported version, rule violation) is listed with
  the reason in `error` (and logged at debug level) instead of breaking the
  list; creating from it fails with the same reason.
- **Create from template** (`POST /projects` with `template_id`) is
  all-or-nothing: everything is validated in memory first, then
  `project.json`, the modules and empty link-pair files are written into
  `.reqman-staging/<random>/` and moved into the data directory with one
  `os.rename`. The staging directory is always removed, so a failure leaves
  nothing behind. Module ids are slugged from the module names, link
  configuration ids from the link names (each with its system mirror
  attribute), and view `link:<key>` entries are mapped to those ids.
- **Save as template** (`POST /projects/{id}/save-as-template`) writes
  `_templates/<slug-of-name>.json` (atomic write, two-space indent, trailing
  newline, fixed key order): non-system attributes except `object_text`,
  modules (`key` = module id, in the project's module order), link configurations (between
  module keys) and views (mirror attributes as `link:<key>`; references to
  attributes the project no longer has are dropped). Requirements, id
  counters and the parent are not saved. The result is round-tripped
  through the loader before it is written. A name already used by any
  template (built-in or user, case-insensitive) is rejected with 409; a
  file is never overwritten.
- **Import a template file** (`POST /templates/import`, WS
  `template.import`; File ▸ Load template ▸ Import template file) saves an
  uploaded template JSON document as a user template
  (`ProjectService.import_template`). It is checked exactly like a loaded
  template (UTF-8, an optional BOM, at most 1 MiB of UTF-8 over REST and
  WebSocket alike, no lone surrogate escapes, shape, `format_version`,
  every project rule) and then written like Save as template: `_templates/<slug-of-name>.json`
  (slug suffixed if the file name is taken), the file's `id` key replaced by
  that stem, canonical key order (unknown keys are not kept). Invalid input
  → 400, a name used by any template → 409, nothing is written in either
  case; success broadcasts `templates_changed`.

### Format versions

`project.json` and the app config file (`.reqman/config.json`) start with
an integer `format_version` (`reqman/formats.py`: `PROJECT_FORMAT`,
`CONFIG_FORMAT`, both currently 1). Module, requirement and link-pair files
have no version of their own; they are governed by their project's
version. Templates keep their own, separate `format_version`.

- A file without the key is version 1 (the format written before versions
  existed) and loads unchanged.
- Loading never rewrites a file: `formats.upgrade` applies the registered
  migration steps (`FileFormat.migrations[n]`: version `n` to `n + 1`) in
  memory; the current version is written by the next real save, so reads
  leave git diffs quiet.
- A file with a newer version than the tool supports raises
  `NewerFormatError` ("... was written by a newer ReqManTool ...": REST
  409, WebSocket -32602). `ProjectStore` checks the project's stored
  version before every read or write of the project's files, and
  `config.save_data_dir` checks the config file, so such files are never
  misread or overwritten. A newer config file stops the server at start
  (unless `REQMAN_DATA_DIR`/`--dir` is given) rather than silently using
  another data directory.
- A `project.json` that cannot be loaded for any other reason (not valid
  UTF-8 JSON, not a JSON object, an invalid `format_version`, a missing
  required key or a value of the wrong shape, or a file this process cannot
  read: no permission, a directory) raises `ValueError` (REST 400, WebSocket
  -32602) on every read or write of that project's files, never treated as
  version 1. The per-file check runs the same upgrade and validation as
  loading the project, so it fails exactly when loading does. An unreadable
  file is reported as "Project '<id>' has an unreadable project.json:
  <reason>", without its absolute path.
- **Project list stubs**: `GET /projects` (WS `project.list`) never fails as
  a whole because of one project. A project whose `project.json` cannot be
  loaded (newer version, invalid JSON, wrong shape, invalid
  `format_version`, unreadable, or a project folder this process cannot
  search) is listed as `{id, name, error}`
  (`ProjectStub`; `name` is the stored name if usable, else the id), always
  top level since its stored parent cannot be trusted; its readable
  subprojects stay under it. A folder this process cannot search is listed
  only if its name is a project slug, so system folders such as
  `lost+found` or `.Spotlight-V100` are not. A stub is fully read-only:
  opening it, reading, listing or changing any of its modules, requirements
  or links (including ones that do not exist), and purging it or one of its
  modules fail with the same error (no write, no broadcast). Purging a
  project, or a module of a project, that does not exist is 404 (WebSocket
  -32001); purging a module that does not exist in a loadable project
  purges nothing (`{"purged": 0}`). The sidebar shows it greyed,
  with a warning icon and the error inline (and in a tooltip on hover,
  focus or touch), and no modules or "New module" entry; clicking or
  activating it opens the project page, which shows the load error. Parent
  pickers list it disabled.
- Every data file (`project.json`, module, requirement, link-pair, template
  and config files) is read as `utf-8-sig` (an optional UTF-8 BOM is
  ignored) and written as plain UTF-8 without a BOM.
- Bump a version for a change an older reader would misinterpret, and for
  any new persisted key that must survive a round-trip through an older
  tool: `Project.from_dict`/`to_dict` drop unknown keys, so a versioned
  tool that loads and saves a project would silently delete a key added
  later without a bump. `module_order` was added without a bump only
  because the tools it could be lost through predate versioning and cannot
  be protected anyway; from this release on, new keys bump the version
  (module folders therefore must). A project migration that changes
  module, requirement or link-pair files must upgrade those files too.

### Module order

`project.module_order` stores the module display order. `GET
/projects/{id}/modules` (and exports, save-as-template) apply it
tolerantly: entries naming missing modules or repeating one are skipped,
and modules it does not list follow in id order, so older projects (no
key) list in id order as before. New modules are appended; a project
created from a template keeps the template's declared module order.
`POST /projects/{id}/modules/{module_id}/reorder` (`{"direction": "up" |
"down"}`, WebSocket `module.reorder`) moves a module one place and stores
the whole effective order; a move past either end writes nothing.

Planned evolution: module folders (a later backlog item) will turn
`module_order` into ordered entries that are either modules or folders,
through a `PROJECT_FORMAT` 1 → 2 migration (for example, each id string
becomes `{"module": id}`, and folders are `{"folder": ..., "children":
[...]}`). The migration runs on the raw dict in `formats.upgrade()`,
before `Project.from_dict`.

### Git-friendly persistence

Every project folder is meant to be a git repository (branches, history,
code review of requirement changes — see "Local-first" above and "Project
history (git)" below). Every JSON write
(`ProjectStore.save_*`/`save_link_pairs`/`config.save_data_dir`) uses
`indent=2` and a trailing newline, and dataclass field order is stable, so
diffs are minimal and readable instead of single-line blobs or
reordered-key noise.

Each `ProjectStore` write is atomic: the JSON goes to a temporary file in
the same directory, which is flushed and synced and then replaces the
target with `os.replace`. Syncing uses `fcntl(F_FULLFSYNC)` where it exists
(macOS, where `os.fsync` only reaches the drive cache), falling back to
`os.fsync` if that fails, and `os.fsync` everywhere else. The directory is not `fsync`ed, so after a power
loss right after a write the target may still hold its previous, complete
contents.
Operations that create several requirements (`add_requirement`,
`copy_requirement`, `import_csv`) go through
`ProjectService._write_new_requirements`: all requirement files are written
first, and `project.json` (with the advanced `id_counters`) last. If a write
fails, the requirement files already written by that operation are deleted
again and the error is re-raised, so a failed operation leaves the module
and the counters unchanged. `_allocate_requirement_id` also skips any id that
already exists on disk, so a crash between the two steps can never cause an
id to be reused.

## Project history (git)

Every project folder is its own git repository (modules are plain folders,
never submodules), driven through the `git` command line
(`backend/reqman/git_repo.py`, no Python dependency). Subprojects are
sibling folders in the data directory (`parent_project_id` is only a field),
so a project's repository never contains another project's files.

- **States** (`GET /projects/{id}/git`, WS `project.git_status`): `ready`
  (own repository on branch `current`: every change is committed), `paused`
  (own repository on another branch, a detached HEAD, or busy — see below:
  files are saved, no commits), `no_repo` (plain files; "Enable history (git)" creates the
  repository), `nested`, `foreign` (see below), `git_missing` (plain files,
  warning shown), `off` (`REQMAN_GIT=off`) and `error` (git cannot inspect
  the folder, e.g. "dubious ownership").
- **Paused while a merge, cherry-pick, revert or rebase is in progress, or
  with unresolved conflicts.** If `MERGE_HEAD`, `CHERRY_PICK_HEAD`,
  `REVERT_HEAD`, `sequencer/`, `rebase-merge/` or `rebase-apply/` exists
  in the git dir, or `git ls-files -u` lists unmerged entries (anywhere,
  not only data files, e.g. after a `git stash pop` conflict — a deliberate,
  conservative choice: `commit --only` would still work outside a merge,
  but the user is visibly mid-resolution), the project is `paused` with a
  message naming the operation ("A merge is in progress in the project
  repository: auto-commit is paused … Finish or abort it and resolve any
  conflicts to resume history"). Staging would otherwise mark the user's
  conflicts as resolved with the markers inside. The check runs again right
  before `git add`, so an operation started meanwhile skips the commit. Files are still saved;
  nothing is staged or committed, "Enable history" is 409, and the first
  change after the user finished commits everything saved meanwhile. The
  check costs a few file checks plus `git ls-files -u` twice per mutation.
- **Nested work trees are never touched.** If the data directory lies
  inside another repository's work tree (`git rev-parse --show-toplevel`
  from the project folder's parent succeeds — for example a data directory
  inside the ReqManTool checkout, or a data directory the user versions as
  one repository), the project is `nested`: no `git init`, no commit, even
  if the project folder has a repository of its own. The status names the
  enclosing repository and tells the user to move the data directory.
  Every writing git command names the project's repository explicitly
  (`--git-dir`/`--work-tree`) and inherited `GIT_DIR`/`GIT_WORK_TREE`/...
  variables are dropped, so git can never fall back to an outer repository.
  Discovery crosses file systems (`GIT_DISCOVERY_ACROSS_FILESYSTEM=1`), so
  an enclosing repository on another mount is still found.
- **Only the folder's own `.git` directory.** The project's repository is
  used only if `<project folder>/.git` is a real directory (not a symlink)
  and `git rev-parse --absolute-git-dir` and `--git-common-dir` both
  resolve to it (compared with the folder's real path plus `.git`, without
  following a `.git` link). A `.git` *symlink* or *file* (`gitdir:
  ../other/.git`, a separate git dir) or a linked worktree (`git worktree
  add`) would make a commit or `git checkout -b current` change another
  repository's HEAD, index and refs, so such a folder is `foreign`: "the
  project folder's .git points to another repository (separate git dir or
  linked worktree); history is not supported for it". No init, commit or
  checkout ever; files are saved as plain files; "Enable history" is 409
  (WS -32602). Supporting separate git dirs is a later product question.
- **New projects** (blank, from a template, subprojects) get `git init`,
  `HEAD` → `refs/heads/current`, `*.tmp` in `.git/info/exclude` (the
  temporary files of atomic writes) and an initial commit "Create project
  '<name>'". A template project is created in `.reqman-staging/` and
  renamed in; the repository is created after the rename. Existing projects
  get "Enable history (git)" (`POST /projects/{id}/git/enable`, WS
  `project.git_enable`, broadcasts `changed`): a folder without a
  repository is initialised the same way ("Enable history (git) for project
  '<name>'"); a repository of the user's on another branch gets a new
  `current` branch at its HEAD (`git checkout -b`, which changes no file);
  an already enabled project only commits pending changes. A nested or
  foreign folder, a merge/cherry-pick/revert/rebase in progress or
  unresolved conflicts, a detached HEAD, or an existing `current` branch
  that is not checked out is refused with 409 before anything changes (ReqManTool
  never switches branches, since that changes files); git turned off or
  missing is 400. A step that fails part-way (e.g. the repository was
  created but the initial commit failed) is 400 with the error recorded as
  `last_error`, and still broadcasts `changed` (REST and WebSocket), since
  the status changed.
- **Auto-commit.** Every public mutating `ProjectService` method is wrapped
  in `_records_history`: `ProjectStore.recording_writes` notes the projects
  whose files the operation wrote or deleted, and after the operation
  succeeded each is committed (REST and WebSocket alike; a failed operation
  commits nothing). Only data files are staged, committed and counted as
  pending: `git status`, `git add -A` and `git commit --only` take the
  pathspecs `:(literal)project.json`, `:(glob)modules/*/module.json`,
  `:(glob)modules/*/requirements/[!.]*.json` and `:(glob)links/[!.]*.json`
  (those that match a changed file), and the status output is filtered the
  same way, so `.DS_Store`, macOS AppleDouble files (`._X.json`, also
  skipped when the store lists requirements), `Thumbs.db`, editor backups (`*~`, `*.swp`), other
  files in the folder and anything the user staged themselves are left
  alone; deleted data files are still staged. Signing is off
  (`commit.gpgsign=false`) and **no hook ever runs**: every git command gets
  `-c core.hooksPath=/dev/null` (overriding the repository's
  `.git/hooks` and any `core.hooksPath` from repository, global or system
  config; on Windows, where git would look in a `dev\null` folder of the
  drive root, it is an empty directory made once per server process with
  `tempfile.mkdtemp` in the user's temp folder, made again under the same
  name if it goes missing, and `/dev/null` for the rest of the process if
  it cannot be made or is no real directory),
  inherited `GIT_CONFIG_PARAMETERS`/`GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_*`/
  `GIT_CONFIG_VALUE_*` are dropped so they cannot override it, and `git
  init` runs with `--template=` so no template hook (`init.templateDir`) is
  copied into a new repository. `-c core.fsmonitor=false -c gc.auto=0 -c
  maintenance.auto=false` keep a configured fsmonitor program and automatic
  gc/maintenance from running too. `.gitattributes` filters (e.g. Git LFS)
  are honoured on purpose: they define how the user's repository stores
  files. An auto-commit never waits for input or a hook. Inherited `GIT_*_PATHSPECS` variables are dropped too; commands
  given a requirement file path run with `GIT_LITERAL_PATHSPECS=1`. Data
  files edited by hand are included in the next commit and described like
  any other change.
- **Messages** are generated from the changed files (`history.py`:
  `describe_changes`, `commit_message`), e.g. "SYRS-0012: Status Draft →
  Reviewed; SYRS-0013 added": requirement added/removed/moved/reordered and
  per-attribute old → new values (link mirror attributes are left out; the
  link file entry "Link A → B added (Satisfies)" covers them), modules,
  attributes, link configurations, views and module order. Entries go into
  the subject while it stays within 72 characters ("; +N more"), the full
  list into the body (at most 200 lines). Auto-commits end with a
  `ReqMan-Session: <id>` trailer.
- **Squash.** A change amends the previous commit instead of adding one
  when that commit has the `ReqMan-Session` trailer (so never the initial
  commit or a user's commit), exactly one parent, the same author name and
  email, a committer time within the squash window (default 10 minutes,
  measured from the previous change, so a session lasts while changes keep
  coming), and no ref other than `refs/heads/current` contains it (no tag
  such as `baseline/<name>`, no `baseline` or other branch, no
  remote-tracking branch, no stash). The amended message describes the
  whole session (the session's parent → now). If a session's changes cancel
  out, the session commit is dropped (`git reset --soft HEAD~1`).
- **Author**: `REQMAN_GIT_AUTHOR_NAME`/`REQMAN_GIT_AUTHOR_EMAIL`, each
  falling back to what git itself would use (`git var GIT_AUTHOR_IDENT`:
  `GIT_AUTHOR_*`, the repository's and the user's git config), then to the
  login name and `<login>@localhost`.
- **Settings are environment variables** (read at start-up,
  `GitSettings.from_env`): `REQMAN_GIT=off` turns history off,
  `REQMAN_GIT_SQUASH_MINUTES` sets the window (`0` never squashes),
  `REQMAN_GIT_AUTHOR_NAME`/`_EMAIL` the author, `REQMAN_GIT_EXECUTABLE`
  the git command. They are deliberately not keys of `.reqman/config.json`:
  a new persisted key needs a `CONFIG_FORMAT` bump, after which an older
  ReqManTool refuses to start with that config file, which is too high a
  price for settings that have good defaults; a settings UI (later) can
  add them with a bump and a migration.
- **Failures never lose data.** The files are written before git runs. A
  git error, or any unexpected error (e.g. while describing a hand-edited
  file; the message generator also tolerates wrong JSON types), never fails
  the mutation or project creation: it is logged and kept in memory as `last_error` (shown in the
  status, cleared by the next successful commit); since every commit
  stages all changed data files, the next change retries it. The status
  also reports `pending_changes` (data files that differ from HEAD), which
  survives a restart.
- **Concurrency and cost.** Writing git operations are serialized per
  project folder by a lock. Reads (status, history) take no lock and run
  with `GIT_OPTIONAL_LOCKS=0`. A mutation costs about 11–12 short git
  processes (state checks, status, HEAD, squash checks, the in-progress
  re-check, add, diff, cat-file, commit),
  roughly 80 ms on macOS; mutations run on the server's event loop, so git
  runs inline before the response.
- **History** (`GET /projects/{p}/modules/{m}/requirements/{r}/history`,
  WS `requirement.history`, read-only, no broadcast): `git log
  --first-parent` of the requirement file (newest first, at most
  `HISTORY_LIMIT` = 200 commits, `truncated` beyond) with each revision read
  through one `git cat-file --batch`; the JSON is compared revision by
  revision into `entries` (`change`: `added`/`modified`/`removed`, `diffs`
  per attribute, parent and order). **First-parent semantics:** only the
  commits of `current`'s first-parent chain are listed, each compared with
  its first parent, so a merge is listed with what it brought into
  `current` (its author and subject), the commits of a merged side branch
  are not listed themselves, and diverged branches cause no phantom diffs.
  No `--follow`: requirement files are never renamed (ids are never reused
  and requirements never move between modules), and rename detection would
  mistake a requirement removed and another added in one squashed commit
  for a rename. As a guard, history stops at a revision whose content names
  another requirement id; the oldest own revision is then listed as
  `added` (not `truncated`). `blame` maps each current attribute to the
  last listed commit that changed it. `uncommitted` is true when the file
  differs from HEAD. A project without a readable repository (off, git
  missing, no repository, nested, foreign) answers `available: false` with
  the `reason`, not an error; a missing requirement is 404.
- Not yet (later slices): baselines (`baseline` branch, `baseline/<name>`
  tags, browse/compare), the project Git panel, remotes.

## Backend layering

```
backend/reqman/
  models.py       Plain dataclasses — the persisted shape. No behavior.
  store.py        ProjectStore — disk I/O only (read/write/list/delete
                   JSON files). No business rules, no validation beyond
                   "does this file exist."
  service.py      ProjectService — all business rules live here: id
                   generation, attribute validation, nesting/cycle
                   checks, link direction enforcement, cleanup cascades
                   (remove/purge/remove-link-configuration). This is the
                   ONE place REST and WebSocket both call into.
  app.py           FastAPI app: REST routes (thin — parse the request
                   body, call a ProjectService method via the `_wrap`
                   helper which turns FileNotFoundError→404 and
                   ValueError→400, broadcast on success) and the `/ws`
                   WebSocket endpoint (a JSON-RPC 2.0 dispatch table,
                   `RPC_METHODS`, mapping method names to the same
                   ProjectService calls).
  reqif_export.py  Pure function, no reqman.models/service import (duck
                   typing) — builds a ReqIF XML string from plain
                   project/module/requirement/link data. Kept import-
                   isolated on purpose: it's easy to unit-test standalone
                   and can't create a circular import with service.py.
  csv_export.py    Same pattern as reqif_export.py, much smaller.
  xlsx_export.py   Same columns and row order as csv_export.py; string
                   values are stored as string cells, never formulas.
  ordering.py      tree_order(): the shared row order of the CSV and XLSX
                   exports (depth-first, siblings by order then id).
  csv_import.py    The inverse of csv_export.py, same duck-typed pattern:
                   validates CSV text all-or-nothing and returns rows;
                   ProjectService.import_csv allocates ids and writes them.
  templates.py     Project template format: namespaced ids, reading the
                   built-in templates/ folder, shape checks (no domain
                   rules — ProjectService validates by building the project
                   in memory). templates/ holds the built-in JSON files.
  git_repo.py      GitHistory: per-project git repositories through the
                   git CLI (state detection incl. nested work trees, init,
                   auto-commit with squash, status, requirement history).
                   GitSettings: the REQMAN_GIT* environment settings.
  history.py       Pure functions, duck-typed like csv_export.py: commit
                   message entries from changed files, and a requirement's
                   change list + attribute blame from its file revisions.
  config.py        Where the *chosen data directory itself* is persisted
                   (`.reqman/config.json` next to the repo, or
                   REQMAN_DATA_DIR/--dir override) — deliberately
                   separate from ProjectStore, which only knows how to
                   read/write *inside* whatever directory it's given.
  version.py       version_info() → {version, build} for GET /version and
                   WS version.get. version = reqman.__version__ (the one
                   version source; frontend/vite.config.ts parses it at
                   build time). build = REQMAN_BUILD env var, else
                   "<short commit> (<commit date>)" from git (computed once
                   per process, pre-warmed in the FastAPI lifespan at
                   start-up; the backend has no build step, so the commit
                   date is used), else "dev".
```

**Why a service layer at all, instead of putting logic in the route
handlers?** Because every mutation is reachable over both REST and
WebSocket, and they must behave identically (same validation, same
broadcast timing, same error mapping). Business logic in `app.py` would
have to be duplicated or awkwardly shared between two very different route
styles (FastAPI path operations vs. a dict-of-lambdas RPC table);
centralizing it in `ProjectService` means `app.py` on both sides is a thin,
almost mechanical adapter.

### REST API surface

All routes are project-scoped (`/projects/{project_id}/...`) except
`/projects`, `/templates`, `/config`, `/fs/browse`, and `/version`. See `backend/reqman/app.py` for
the authoritative, current route list — the summary here groups them by
resource:

| Resource | Routes |
|---|---|
| Projects | `POST/GET /projects` (`GET` lists unloadable projects as `{id, name, error}` stubs; `POST` body: `name`, `description`, optional `template_id`, optional `parent_project_id`), `GET /projects/{id}`, `PUT /projects/{id}/parent` (`{"parent_project_id": id or null}`) |
| History (git) | `GET /projects/{id}/git` (status; WS `project.git_status`), `POST /projects/{id}/git/enable` (WS `project.git_enable`, broadcasts `changed`, also after a part-way failure; 409 nested/foreign/detached/merge etc. in progress/`current` not checked out, 400 git off or missing or a failed step), `GET .../modules/{m}/requirements/{r}/history` (WS `requirement.history`) — see "Project history (git)" |
| Templates | `GET /templates` → `[{id, source, name, description, modules: [{key, name, prefix}], error}]`, `POST /projects/{id}/save-as-template` (`{name, description}`; 409 on a name clash; broadcasts `templates_changed`), `POST /templates/import` (raw template JSON request body, `Content-Type: application/json` required (415 otherwise), UTF-8 with optional BOM, at most 1 MiB (413); invalid → 400, name clash → 409; returns the template summary; broadcasts `templates_changed`; WS: `template.import` `{text}`) |
| Attributes | `POST /projects/{id}/attributes` |
| Link configurations | `POST/GET /projects/{id}/link-configurations`, `DELETE .../link-configurations/{config_id}` |
| Modules | `POST/GET /projects/{id}/modules`, `GET .../modules/{id}`, `POST .../modules/{id}/purge`, `POST /projects/{project_id}/modules/{module_id}/reorder` (`{"direction": "up" \| "down"}`; WS: `module.reorder`) |
| Requirements | `POST/GET .../requirements`, `GET/PUT/DELETE .../requirements/{id}`, `PUT .../requirements/{id}/parent` (move), `POST .../requirements/{id}/reorder`, `POST .../requirements/{id}/copy` |
| Links | `POST/DELETE /projects/{id}/links` |
| Export | `GET /projects/{id}/export/reqif`, `GET /projects/{id}/export/csv` |
| Import | `POST /projects/{id}/modules/{module_id}/import/csv` (raw UTF-8 CSV request body, `Content-Type: text/csv` required (415 otherwise), at most 10 MiB (413); WS: `requirement.import_csv`) |
| Purge | `POST /projects/{id}/purge` |
| Data directory | `GET/POST /config`, `GET /fs/browse` |
| Version | `GET /version` → `{version, build}` (WS: `version.get`, read-only, no broadcast) |
| Live sync | `WS /ws` |

### WebSocket JSON-RPC

`/ws` speaks JSON-RPC 2.0: `{"jsonrpc": "2.0", "id": ..., "method":
"requirement.add", "params": {...}}` → `{"jsonrpc": "2.0", "id": ...,
"result": ...}` or `{"jsonrpc": "2.0", "id": ..., "error": {"code": ...,
"message": ...}}`. Method names mirror the REST resources
(`project.create`, `module.create/purge/reorder`, `requirement.add/update/remove/
move/reorder/copy/link/unlink`, `link_configuration.create/list/remove`,
…) — see `RPC_METHODS` in `app.py` for the exact table. Methods listed in
`MUTATING_METHODS` broadcast a `changed` event after success, same as the
REST side (`manager.broadcast`, excluding the sender socket so a client
doesn't get a redundant refresh of its own change). Templates and
subprojects: `template.list`, `project.create` (optional `template_id`,
`parent_project_id`), `project.set_parent` (`{id, parent_project_id}`,
broadcasts `changed`), `project.save_as_template` (`{id, name,
description}`) and `template.import` (`{text}`: the template file's
content), both in `TEMPLATE_MUTATING_METHODS`: they broadcast
`templates_changed` instead of `changed`. A 409 conflict on the REST side
is a `-32602` error over WebSocket, like other validation errors.

## Frontend structure

```
frontend/src/
  api/
    client.ts     fetch wrapper (ApiError, base URL from Vite env vars)
    types.ts       TypeScript mirrors of the backend dataclasses
    queries.ts      Every TanStack Query hook — one per REST call, plus
                    queryKeys and invalidation. This is the ONLY file that
                    should call `api.*` directly.
  hooks/
    useLiveSync.ts  The one app-wide WebSocket connection; invalidates
                    queries on "changed", the template list on
                    "templates_changed", clears the cache on "root_changed"
                    (except the version query: same backend), refetches the version query on reconnect (or on first
                    connect if it failed while the backend was down)
  actions/          The shared action registry (see "Actions, menus and
                    shortcuts" below): types.ts, registry.ts (all actions,
                    menu layouts, shortcut dispatch), appActions.ts
                    (File/View/Help), requirementActions.tsx (Edit and the
                    row context menu), AppActionsProvider.tsx (state,
                    dialogs, global shortcuts), ActionMenuEntries.tsx (renders
                    a menu layout), shortcuts.ts (matching, display, typing
                    guard)
  layout/
    AppLayout.tsx   Header (title, menu bar, data-folder settings, color
                    scheme) + Sidebar + routed <Outlet/> + VersionFooter
                    (frontend version/build, backend-version mismatch
                    warning), all inside AppActionsProvider
    MenuBar.tsx     File / Edit / View / Help, rendered from MENU_BAR
    AppDialogs.tsx  The dialogs actions open (new project, data folder,
                    save as / import template, import CSV, shortcuts, about)
  buildInfo.ts      FRONTEND_VERSION_INFO from the Vite `define` globals
                    __REQMAN_VERSION__/__REQMAN_BUILD__ (declared in
                    vite-env.d.ts, computed in vite.config.ts)
  components/       Modals and small reusable pieces (one file per modal:
                    NewProjectModal, NewModuleModal, AddAttributeModal,
                    LinkConfigurationModal, RequirementFormModal, LinkModal,
                    MoveRequirementModal, FolderPickerModal,
                    SaveAsTemplateModal, ProjectParentModal,
                    EditableAttributeCell, DataDirSettings, Sidebar).
                    NewProjectModal picks an optional template and parent;
                    Sidebar renders projects as a tree by
                    parent_project_id (projectTree.ts: forest building,
                    indented project options, descendant lookup)
  pages/
    HomePage.tsx     redirects to the first project, or an empty state
    ProjectPage.tsx  project overview: parent/subprojects, history (git)
                     status line (GitStatusPanel: state, last commit,
                     pending changes, warnings, Enable history), attributes,
                     link configurations, views, modules; New subproject,
                     Change parent and Save as template actions
    ModulePage.tsx   the requirements tree/table — the largest page;
                     owns the tree-flattening logic and renders the
                     right-click context menu from the action registry;
                     the detail panel is RequirementDetailPanel (Details
                     tab with "last changed by/when" label tooltips from the
                     attribute blame, History tab with per-attribute diffs)
```

Server state (projects/modules/requirements/config/link-configurations)
lives entirely in TanStack Query's cache — there is no separate Redux/
Zustand store. Local UI-only state (which modal is open, collapsed tree
nodes) is plain `useState` in the owning component; it isn't lifted higher
than it needs to be. State that the menu bar, context menus and shortcuts
share — the requirement selection and copy/cut clipboard, the active
attribute view, the detail panel toggle (remembered per browser in
`localStorage`), the colour scheme and the app-wide dialogs — lives in
`AppActionsProvider`.

### Actions, menus and shortcuts

Every command reachable from more than one place is an `Action` in one
registry (`frontend/src/actions/registry.ts`): `id`, `element` (element
type), `label`, optional `icon` and `shortcuts`, `isAvailable(ctx)`,
`run(ctx)`, and optionally `isChecked(ctx)` (toggles) or `items(ctx)`
(a generated submenu, e.g. one entry per view or template). The context is
the provider's `AppState` (route ids, loaded project/module/requirements,
selection, clipboard, views, templates, `commands`) plus a `target` whose
type depends on the element type: nothing for `app` actions, a requirement
for `requirement` actions. Explicit targets are passed keyed by element
type (`run(id, { requirement: row })`, `<ActionMenuEntries targets=…>`), so
each action of a mixed menu gets a target of its own type; without an
entry, `resolveTarget` supplies the default. Menus are data (`MenuEntry[]` layouts:
`MENU_BAR`, `REQUIREMENT_CONTEXT_MENU`) rendered by `ActionMenuEntries`, so
the menu bar and the row context menu show the same labels, shortcut hints
and enabled state. The menu bar and shortcuts target the selected
requirement; a context menu passes the clicked row as its `requirement`
target. Toolbar buttons (Paste at root, Import CSV, the module page's
Export buttons, header data-folder and colour-scheme buttons, New project
in the sidebar, New subproject on the project page) call `run(id)` too.
The clipboard is scoped to the data folder it was filled in, and a data
folder switch (in this tab, or a `root_changed` live-sync event) clears the
clipboard and the selection, since ids repeat across folders.

Shortcuts are dispatched by one `keydown` listener in the provider: the
first registered action with a matching shortcut that is available runs;
a binding marked `whenNoTarget` only counts while the action's default
target is empty (so Ctrl/Cmd+V pastes as child of the selection — never
onto the clipboard requirement itself — or at the root only when nothing
is selected; with a selection it never falls through to the root). They are ignored while focus is in an input,
textarea, select, contenteditable element, the inline cell editor
(`data-inline-editor`), a combobox/listbox, an open menu or a dialog, and
Ctrl/Cmd+C/X are left to the browser while page text is selected.

To add a context menu for another element (sidebar node, column header,
tab): add the element type to `ActionTargets` with its target type, add its
actions to the registry, add a `resolveTarget` case for the menu bar /
shortcuts, and describe the menu as a `MenuEntry[]` layout rendered with
`targets={{ <element>: clicked }}`.

The menu bar follows the WAI-ARIA menubar pattern: `role="menubar"`, one
tab stop (roving tabindex), ArrowLeft/ArrowRight (Home/End) between the
top-level menus, ArrowDown opens one, and with a menu open ArrowLeft/
ArrowRight open the adjacent menu. Modified keys on a menu title (e.g.
Alt+ArrowDown) are left to the app shortcuts. Escape (also inside a
submenu) returns focus to the menu title; ArrowLeft in a submenu returns it
to the submenu's item (`SubMenu` in `ActionMenuEntries.tsx` works around a
Mantine 9.6 bug that would drop focus to `<body>`).

`ModulePage.tsx`'s requirements table is a **flattened tree**, not nested
JSX: `flattenTree()` walks the parent/child structure (sorted by `order`)
into a flat `{requirement, depth, hasChildren}[]`, respecting a
`collapsed: Set<string>` of hidden subtrees, and renders one `<Table.Tr>`
per visible row with `paddingLeft: depth * 20` on the first attribute cell.
This was a deliberate choice over recursive JSX so the result is still a
real `<table>` (sortable columns, consistent row actions, no manual
virtualization concerns) rather than a tree widget bolted onto a table.

## Extending the system

The `reqman-feature` and `reqman-validation` Claude Code skills in
`.claude/skills/` encode the implementation and validation checklist this
project follows — read them before making a change. In short:

1. Model change → `models.py` (+ `store.py` if new file I/O is needed).
2. Business rule → `service.py`. This is where validation, id allocation,
   cascade cleanup, and cross-cutting invariants belong — never in a route
   handler.
3. Expose it → a REST route *and* (if it's a mutation) an RPC method in
   `app.py`, both calling the same service method, both added to
   `MUTATING_METHODS`/broadcasting if they mutate.
4. Frontend → a typed function in `api/queries.ts`, then the UI.
5. Tests → `backend/tests/test_service.py` for business rules,
   `backend/tests/test_api.py` for the REST/WebSocket contract. Every
   feature in this codebase so far has both.

## Testing

- `backend/tests/test_service.py` — exercises `ProjectService` directly
  against a `ProjectStore` over a `tmp_path`, no HTTP involved. This is
  where most business-rule edge cases (cycle detection, direction
  enforcement, id allocation, cleanup cascades) are covered.
- `backend/tests/conftest.py` — runs every test with `REQMAN_GIT=off` and
  git isolated from the user's configuration (`GIT_CONFIG_GLOBAL=/dev/null`,
  `GIT_CONFIG_NOSYSTEM=1`, fixed author/committer). `test_git_history.py`,
  `test_git_history_edge_cases.py`, `test_git_history_safety.py` and
  `test_git_history_conflicts_and_isolation.py` opt in, with repositories
  in `tmp_path` only (never in the checkout).
- `backend/tests/test_api.py` — exercises the FastAPI app via
  `TestClient`, including WebSocket round-trips and the live-sync
  broadcast (opens two WebSocket connections, mutates via REST, asserts
  the second connection receives the `changed` event).
- Frontend: `npm run build` (TypeScript strictness catches most
  regressions) and `npm run lint`; there is no frontend test suite yet.

Run everything with `cd backend && .venv/bin/python -m pytest -q` and
`cd frontend && npm run build && npm run lint`.
