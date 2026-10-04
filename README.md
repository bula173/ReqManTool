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
  context menu
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

[MIT](LICENSE)
