# ReqManTool

A local-first, DOORS-inspired requirements management tool for small teams —
projects, modules, hierarchical requirements, custom attributes, directional
traceability links, and ReqIF/CSV export. Requirements are stored as plain
JSON files on disk, so history, review, and backup are just... git.

## Features

- **Projects → Modules → Requirements**, with any requirement able to have
  children (nesting/"chapters" within a module)
- **Custom attributes** per project (string/text/integer/boolean/enum),
  applied to every module
- **DOORS-style link configurations** — name a directional link type
  between two specific modules; linking then requires one of them, with
  module and direction enforced. Linked requirements show `>` (outgoing) /
  `<` (incoming) markers
- **Copy / cut / paste and reordering** for requirements, via a right-click
  context menu, the Edit menu, or keyboard shortcuts (Ctrl/Cmd+X/C/V,
  Alt+↑/↓)
- **Application menu bar** — File (new project, open project folder, load /
  import / save templates, CSV import, exports), Edit (clipboard, reordering,
  purge module / project), View (attribute view,
  detail panel, dark mode) and Help (shortcuts, documentation, About)
- **Project templates** — create a project from a template (attributes,
  modules, link configurations and views, no requirements), including the
  built-in `RBC_Template` for an ERTMS/ETCS RBC under EN 50126/50128/50129;
  save any project's structure as your own template, or import a template
  JSON file (stored in the data folder's `_templates/`)
- **Subprojects** — arrange projects as a generic → specific tree (e.g.
  generic product → generic application → specific application), shown as
  a tree in the sidebar
- **Export to ReqIF** (using the [`reqif`](https://github.com/strictdoc-project/reqif)
  package) and **CSV**
- **Live sync** — edits in one browser tab/window show up in others without
  a manual refresh
- A **REST API and a WebSocket JSON-RPC API**, both backed by the same
  business logic, so scripting against ReqManTool doesn't require the UI
- **No database, no accounts** — point it at a folder (switchable from the
  UI, including a folder-picker dialog), and that folder *is* your data;
  manage its history with git yourself

## Quickstart

```sh
git clone <this-repo>
cd ReqManTool
./etc/start.sh
```

This starts the backend on `http://localhost:8899` and the frontend dev
server on `http://localhost:5173` — open the latter in a browser. First run
installs a Python venv (`backend/.venv`) and `npm install`s the frontend
automatically. See `etc/start-backend.sh` / `etc/start-frontend.sh` to run
either half on its own (e.g. two terminals).

By default, data is stored in `example_database/` at the repo root; change
it any time from the folder icon in the app header (or `--dir <path>` /
`REQMAN_DATA_DIR` when starting the backend).

## Version

The tool version lives in one place, `__version__` in
`backend/reqman/__init__.py`; the frontend build reads it from there. The
app footer shows `ReqManTool <version> · build <commit> (<date>)` and warns
when the frontend and backend versions differ. `GET /version` (WebSocket
`version.get`) returns the backend's `{version, build}`. The backend's build
date is the date of the checked-out commit; the frontend's is the date of
`npm run build`. `npm run dev` shows build `dev`. Set `REQMAN_BUILD` to
override the build id (backend: its process environment; frontend: at
`npm run build` and `npm run dev`); without git it falls back to `dev`.

## Project layout

```
ReqManTool/
├── backend/    # Python/FastAPI: REST + WebSocket JSON-RPC API, business logic, disk storage
├── frontend/   # React/TypeScript/Vite UI
├── etc/        # start.sh / start-backend.sh / start-frontend.sh
└── docs/
    ├── ARCHITECTURE.md   # how the system is built — read this first if contributing
    └── BACKLOG.md        # what's done, what's planned, and why
```

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — data model, disk layout,
  backend layering, API surface, frontend structure, how to extend the
  system
- [`docs/BACKLOG.md`](docs/BACKLOG.md) — feature status and a DOORS-derived
  reference checklist for what a mature requirements tool has

## Testing

```sh
cd backend && .venv/bin/python -m pytest -q
cd frontend && npm run build && npm run lint
```

## Status

Actively developed; core requirement/module/link/nesting/export workflows
are implemented and tested. See `docs/BACKLOG.md` for what's next.

## License

Dual-licensed: [AGPL-3.0-only](LICENSE) for the community (local)
edition, or a commercial licence. The server edition is commercial only.
See [LICENSING.md](LICENSING.md).
