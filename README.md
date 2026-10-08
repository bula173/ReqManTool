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
- **Collapsible, resizable side panels** — the navigation tree (left) and the
  requirement detail panel (right) collapse to a thin rail via the chevron on
  their border, the View menu, or Ctrl/Cmd+B / Ctrl/Cmd+Alt+B; drag the border
  to resize. State and widths are kept per browser; on narrow screens the
  panels open as overlays
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

## Install (release package)

Each `v*` release on GitHub carries a wheel that contains the backend *and*
the built frontend, served together from one origin. No root/admin rights
are needed:

```sh
pipx install <release wheel URL>     # or: uv tool install <release wheel URL>
reqman init                          # create the per-user config file and data directory
reqman                               # same as `reqman serve`; open http://localhost:8899
```

`reqman init [--data-dir PATH] [--host H] [--port N] [--cors-origins A,B]`
writes the config file (an existing one keeps its values; flags replace
them) and creates the data directory. Default locations:

| platform | config file | data directory |
|---|---|---|
| Linux | `~/.config/reqman/config.json` (`$XDG_CONFIG_HOME`) | `~/.local/share/reqman` (`$XDG_DATA_HOME`) |
| macOS | `~/Library/Application Support/ReqManTool/config.json` | `~/Library/Application Support/ReqManTool/data` |
| Windows | `%APPDATA%\ReqManTool\config.json` | `%APPDATA%\ReqManTool\data` |

The config file holds `data_dir`, `host`, `port` and `cors_origins`. Each
setting is resolved as CLI flag > environment variable (`REQMAN_DATA_DIR`,
`REQMAN_HOST`, `REQMAN_PORT`, `REQMAN_CORS_ORIGINS`) > config file > default
(`localhost:8899`). `--config PATH` / `REQMAN_CONFIG_FILE` selects another
config file; `reqman config` prints the effective settings and where each
came from. The folder icon in the app header writes `data_dir` back to the
same file, keeping its other keys.

To run it in the background, `reqman service install` registers a per-user
service (a systemd `--user` unit on Linux, a launchd agent on macOS) that
runs `reqman serve` with the current config file; `reqman service uninstall`
removes it. Add `--dry-run` to print the unit/plist and the commands
without changing anything, or `--no-activate` to only write the file.

To build the release artefacts yourself: `pip install build`, then
`etc/build-dist.sh [OUTDIR]` (builds the frontend, bundles it into
`reqman/static`, and writes the wheel and sdist to `dist/`).

## Quickstart (from a git checkout)

```sh
git clone <this-repo>
cd ReqManTool
./etc/start.sh
```

This starts the backend on `http://localhost:8899` and the frontend dev
server on `http://localhost:5173` — open the latter in a browser. First run
installs a Python venv (`backend/.venv`, runtime dependencies from
`backend/requirements.txt`) and `npm install`s the frontend automatically.
See `etc/start-backend.sh` / `etc/start-frontend.sh` to run either half on
its own (e.g. two terminals). `etc/start-backend.sh` accepts the
`reqman serve` flags (`--dir`, `--host`, `--port`, `--cors-origins`,
`--config`).

By default, data is stored in `example_database/` at the repo root; change
it any time from the folder icon in the app header (or `--dir <path>` /
`REQMAN_DATA_DIR` when starting the backend). A checkout keeps using an
existing checkout-local `.reqman/config.json` (the location used before the
per-user config file existed); without one it uses the per-user config file
described above. The dev frontend (`npm run dev`) talks to
`http://localhost:8899` unless `VITE_API_BASE_URL` / `VITE_WS_URL` say
otherwise; a production build (`npm run build`) talks to the origin that
serves it.

## Version

The tool version lives in one place, `__version__` in
`backend/reqman/__init__.py`; the frontend build reads it from there. The
app footer shows `ReqManTool <version> · build <commit> (<date>)` and warns
when the frontend and backend versions differ. `GET /version` (WebSocket
`version.get`) returns the backend's `{version, build}`. The backend's build
date is the date of the checked-out commit; the frontend's is the date of
`npm run build`. `npm run dev` shows build `dev`. Set `REQMAN_BUILD` to
override the build id (backend: its process environment; frontend: at
`npm run build` and `npm run dev`); without git it falls back to `dev`. A
release package records its build id in `reqman/BUILD` at build time.

## Project layout

```
ReqManTool/
├── backend/    # Python/FastAPI: REST + WebSocket JSON-RPC API, business logic, disk storage
├── frontend/   # React/TypeScript/Vite UI
├── etc/        # start.sh / start-backend.sh / start-frontend.sh / build-dist.sh
├── .github/    # CI (pytest, frontend build + lint) and the tag-triggered release
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
cd backend && .venv/bin/pip install -r requirements-dev.txt   # pytest, httpx (once)
cd backend && .venv/bin/python -m pytest -q
cd frontend && npm run build && npm run lint
```

Test-only dependencies are in `backend/requirements-dev.txt` (or the `test`
extra of `backend/pyproject.toml`). GitHub Actions runs the same checks on
every push and pull request (`.github/workflows/ci.yml`).

## Status

Actively developed; core requirement/module/link/nesting/export workflows
are implemented and tested. See `docs/BACKLOG.md` for what's next.

## License

Dual-licensed: [AGPL-3.0-only](LICENSE) for the community (local)
edition, or a commercial licence. The server edition is commercial only.
See [LICENSING.md](LICENSING.md).
