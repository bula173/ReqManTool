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
  files on disk, not a database. This is a *feature*: the user manages
  history, backup, branching, and code review of their requirements with
  ordinary git, the same as source code. The backend never assumes a
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

## Data model and disk layout

All persisted types are plain Python `@dataclass`es in
`backend/reqman/models.py`, each with `to_dict()`/`from_dict()` for JSON
(de)serialization. Field order in the dataclass is the JSON key order on
disk (via `dataclasses.asdict`), kept stable on purpose — see "Git-friendly
persistence" below.

```
<data-directory>/
  <project-id>/
    project.json                  # Project: id, name, description,
                                   #   attributes (incl. system ones),
                                   #   id_counters, link_configurations
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
  `description`, `attributes: list[AttributeDefinition]` (applies to every
  module in the project — there is no per-module attribute schema),
  `id_counters: dict[prefix, int]` (requirement id allocation state, one
  counter per module *prefix*, shared across modules that use the same
  prefix), `link_configurations: list[LinkConfiguration]`.
- **AttributeDefinition** — `id`, `name`, `type` (`string | text | integer
  | boolean | enum`), `required`, `default`, `choices` (enum only),
  `system: bool`. `system=True` marks an attribute the service manages
  automatically — currently only the read-only mirrors that link
  configurations create (see "Link configurations" below);
  `ProjectService._validate_attributes` rejects any attempt to set a
  system attribute directly through `add_requirement`/`update_requirement`.
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

### Git-friendly persistence

The data directory is meant to be a git repository the user manages
themselves (branches, history, code review of requirement changes — see
"Local-first" above). Every JSON write
(`ProjectStore.save_*`/`save_link_pairs`/`config.save_data_dir`) uses
`indent=2` and a trailing newline, and dataclass field order is stable, so
diffs are minimal and readable instead of single-line blobs or
reordered-key noise.

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
  config.py        Where the *chosen data directory itself* is persisted
                   (`.reqman/config.json` next to the repo, or
                   REQMAN_DATA_DIR/--dir override) — deliberately
                   separate from ProjectStore, which only knows how to
                   read/write *inside* whatever directory it's given.
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
`/projects`, `/config`, and `/fs/browse`. See `backend/reqman/app.py` for
the authoritative, current route list — the summary here groups them by
resource:

| Resource | Routes |
|---|---|
| Projects | `POST/GET /projects`, `GET /projects/{id}` |
| Attributes | `POST /projects/{id}/attributes` |
| Link configurations | `POST/GET /projects/{id}/link-configurations`, `DELETE .../link-configurations/{config_id}` |
| Modules | `POST/GET /projects/{id}/modules`, `GET .../modules/{id}`, `POST .../modules/{id}/purge` |
| Requirements | `POST/GET .../requirements`, `GET/PUT/DELETE .../requirements/{id}`, `PUT .../requirements/{id}/parent` (move), `POST .../requirements/{id}/reorder`, `POST .../requirements/{id}/copy` |
| Links | `POST/DELETE /projects/{id}/links` |
| Export | `GET /projects/{id}/export/reqif`, `GET /projects/{id}/export/csv` |
| Purge | `POST /projects/{id}/purge` |
| Data directory | `GET/POST /config`, `GET /fs/browse` |
| Live sync | `WS /ws` |

### WebSocket JSON-RPC

`/ws` speaks JSON-RPC 2.0: `{"jsonrpc": "2.0", "id": ..., "method":
"requirement.add", "params": {...}}` → `{"jsonrpc": "2.0", "id": ...,
"result": ...}` or `{"jsonrpc": "2.0", "id": ..., "error": {"code": ...,
"message": ...}}`. Method names mirror the REST resources
(`project.create`, `module.create`, `requirement.add/update/remove/
move/reorder/copy/link/unlink`, `link_configuration.create/list/remove`,
…) — see `RPC_METHODS` in `app.py` for the exact table. Methods listed in
`MUTATING_METHODS` broadcast a `changed` event after success, same as the
REST side (`manager.broadcast`, excluding the sender socket so a client
doesn't get a redundant refresh of its own change).

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
                    queries on "changed", clears the cache on "root_changed"
  layout/
    AppLayout.tsx   Header (title, data-folder settings, color scheme) +
                    Sidebar + routed <Outlet/>
  components/       Modals and small reusable pieces (one file per modal:
                    NewProjectModal, NewModuleModal, AddAttributeModal,
                    LinkConfigurationModal, RequirementFormModal, LinkModal,
                    MoveRequirementModal, FolderPickerModal,
                    EditableAttributeCell, DataDirSettings, Sidebar)
  pages/
    HomePage.tsx     redirects to the first project, or an empty state
    ProjectPage.tsx  project overview: attributes, link configurations, modules
    ModulePage.tsx   the requirements tree/table — the largest page;
                     owns the tree-flattening logic, the copy/cut/paste
                     clipboard, and the right-click context menu
```

Server state (projects/modules/requirements/config/link-configurations)
lives entirely in TanStack Query's cache — there is no separate Redux/
Zustand store. Local UI-only state (which modal is open, the copy/cut
clipboard, collapsed tree nodes) is plain `useState` in the owning
component; it isn't lifted higher than it needs to be.

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
- `backend/tests/test_api.py` — exercises the FastAPI app via
  `TestClient`, including WebSocket round-trips and the live-sync
  broadcast (opens two WebSocket connections, mutates via REST, asserts
  the second connection receives the `changed` event).
- Frontend: `npm run build` (TypeScript strictness catches most
  regressions) and `npm run lint`; there is no frontend test suite yet.

Run everything with `cd backend && .venv/bin/python -m pytest -q` and
`cd frontend && npm run build && npm run lint`.
