# ReqManTool

A local-first, DOORS-inspired requirements management tool for small teams —
projects, modules, hierarchical requirements, custom attributes, directional
traceability links, and ReqIF/CSV export. Requirements are stored as plain
JSON files on disk, so history, review, and backup are just... git.

## Spis treści (Table of Contents)

1. [Deployment Modes](#1-deployment-modes)
   1.1. [Desktop App (Local Edition)](#11-desktop-app-local-edition)
   1.2. [Web App (Server Edition)](#12-web-app-server-edition)
2. [Features](#2-features)
3. [Install (release package)](#3-install-release-package)
4. [Quickstart (from a git checkout)](#4-quickstart-from-a-git-checkout)
5. [Version](#5-version)
10. [Project layout](#10-project-layout)
11. [Documentation](#11-documentation)
12. [Testing](#12-testing)
13. [Security Considerations](#13-security-considerations)
    13.1. [Data Protection](#131-data-protection)
    13.2. [TLS / HTTPS Configuration](#132-tls--https-configuration)
    13.2. [Server Edition Hardening](#133-server-edition-hardening)
    13.3. [Configuration Best Practices](#134-configuration-best-practices)
20. [Status](#20-status)
21. [License](#21-license)

---

## 1. Deployment Modes

ReqManTool runs in two modes:

### 1.1. Desktop App (Local Edition)
- **One-click installers** (`.dmg` macOS, `.msi` Windows, `.AppImage` Linux) via Tauri
- Bundles Python backend + frontend in a native window (Tauri)
- **No installation hassle** — runs a local server on a free port, stops on exit
- **Picks where to run on start**: the *Local instance* (the bundled backend,
  the default) or a *Server address…* the user types — the window then just
  loads that server. A release build asks once on first start, the choice and
  the last address are remembered (app config), and the **app footer** (left:
  current mode → menu with *Local instance* / *Server address…*) switches at
  any time; `tauri dev` never asks and always runs local
- First run asks for data folder (default `~/ReqManData`)
- Auto-updates from GitHub Releases
- Uses system `git` if present; history works offline
- **Use when**: working offline, single-user, or team sharing a folder on a NAS

### 1.2. Web App (Server Edition)
- **Multi-user** with authentication (GitHub, GitLab, OIDC, LDAP, SAML, proxy header)
- Runs on a server (Linux container, VM, bare metal)
- Users log in with existing accounts (GitHub, GitLab, OIDC, LDAP, SAML)
- Projects can be **published/pushed** to GitLab/GitHub/Gerrit from the UI
- Repository permissions map to roles (Viewer/Reviewer/Editor/Project-Admin)
- Project discovery via GitHub/GitLab API (topic `reqmantool`)
- Publish/Push/Pull with login account — no stored credentials
- Runs as a service (systemd/launchd) or container (Docker, Helm, Railway, Render, Azure)
- **Use when**: team collaboration, multi-user, centralized server, CI/CD integration

## 2. Features

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
  package) and **CSV**, and **import a ReqIF document back** — a formatted
  requirement comes back still formatted, and an enum attribute keeps its
  choices
- **Live sync** — edits in one browser tab/window show up in others without
  a manual refresh
- A **REST API and a WebSocket JSON-RPC API**, both backed by the same
  business logic, so scripting against ReqManTool doesn't require the UI
- **No database, no accounts** — point it at a folder (switchable from the
  UI, including a folder-picker dialog), and that folder *is* your data;
  manage its history with git yourself

## 3. Install (release package)

Each `v*` release on GitHub carries a wheel that contains the backend *and*
the built frontend, served together from one origin. No root/admin rights
are needed:

```sh
pipx install <release wheel URL>     # or: uv tool install <release wheel URL>
reqman init                          # create the per-user config file and data directory
reqman                               # same as `reqman serve`; open http://localhost:8899
```

`reqman init [--data-dir PATH] [--host H] [--port N] [--cors-origins A,B] [--tls-cert-file PATH] [--tls-key-file PATH] [--tls-ca-file PATH]`
writes the config file (an existing one keeps its values; flags replace
them) and creates the data directory. Default locations:

| platform | config file | data directory |
|---|---|---|
| Linux | `~/.config/reqman/config.json` (`$XDG_CONFIG_HOME`) | `~/.local/share/reqman` (`$XDG_DATA_HOME`) |
| macOS | `~/Library/Application Support/ReqManTool/config.json` | `~/Library/Application Support/ReqManTool/data` |
| Windows | `%APPDATA%\ReqManTool\config.json` | `%APPDATA%\ReqManTool\data` |

The config file holds `data_dir`, `host`, `port`, `cors_origins`, `source_url`,
`tls_cert_file`, `tls_key_file`, `tls_ca_file`. Each setting is resolved as
CLI flag > environment variable (`REQMAN_DATA_DIR`, `REQMAN_HOST`,
`REQMAN_PORT`, `REQMAN_CORS_ORIGINS`, `REQMAN_SOURCE_URL`,
`REQMAN_TLS_CERT_FILE`, `REQMAN_TLS_KEY_FILE`, `REQMAN_TLS_CA_FILE`) >
config file > default (`localhost:8899`).
`source_url` is the repository linked as "Source code" in the footer and
Help ▸ About (default: this repository); if you run a modified version for
other people, publish your changes and point `source_url` at them (see
[License](#license)). `--config PATH` / `REQMAN_CONFIG_FILE` selects another
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

## 4. Quickstart (from a git checkout)

```sh
git clone <this-repo>
cd ReqManTool
./etc/start.sh
```

This starts the backend on `http://localhost:8899` and the frontend dev
server on `http://localhost:5173` — open the latter in a browser. First run
installs a Python venv (`backend/.venv`, runtime dependencies from
`backend/requirements.txt`) and `npm install`s the frontend automatically.

Pick the edition with a flag: `./etc/start.sh --local` (or `--desktop`) runs
the local edition without login; `./etc/start.sh --server` runs the server
edition, which requires login and refuses to start until a login provider
is configured (`server.auth_providers` in the config file). Without a flag
the mode comes from `REQMAN_MODE` / the config file (default: local). Other
arguments go to `reqman serve`, e.g. `./etc/start.sh --local --dir ~/ReqManData`.
See `etc/start-backend.sh` / `etc/start-frontend.sh` to run either half on
its own (e.g. two terminals). `etc/start-backend.sh` accepts the
`reqman serve` flags (`--dir`, `--host`, `--port`, `--cors-origins`,
`--config`).

The native desktop app (Tauri, `desktop/`) has its own entry point:
`./etc/start-desktop.sh` bundles the Python backend for it and opens
`tauri dev` in a native window, starting the backend and the Vite dev server
first if they are not already running (it stops only what it started itself,
and reuses anything already up). `./etc/start-desktop.sh --build` produces
the installers under `desktop/src-tauri/target/release/bundle/`; extra
arguments go to `tauri dev` / `tauri build`. The packaged app asks on first
start whether to run the local instance or connect to a server, remembers
that choice (and the address) and lets you change it from the tray menu.
See `desktop/README.md`.

By default, data is stored in `example_database/` at the repo root; change
it any time from the folder icon in the app header (or `--dir <path>` /
`REQMAN_DATA_DIR` when starting the backend). A checkout keeps using an
existing checkout-local `.reqman/config.json` (the location used before the
per-user config file existed); without one it uses the per-user config file
described above. The dev frontend (`npm run dev`) talks to
`http://localhost:8899` unless `VITE_API_BASE_URL` / `VITE_WS_URL` say
otherwise; a production build (`npm run build`) talks to the origin that
serves it.

## 5. Version

The tool version lives in one place, `__version__` in
`backend/reqman/__init__.py`; the frontend build reads it from there. The
app footer shows `ReqManTool <version> · build <commit> (<date>)` and warns
when the frontend and backend versions differ; in the desktop app the same
footer carries the local ⇄ server switch. `GET /version` (WebSocket
`version.get`) returns the backend's `{version, build}`. The backend's build
date is the date of the checked-out commit; the frontend's is the date of
`npm run build`. `npm run dev` shows build `dev`. Set `REQMAN_BUILD` to
override the build id (backend: its process environment; frontend: at
`npm run build` and `npm run dev`); without git it falls back to `dev`. A
release package records its build id in `reqman/BUILD` at build time.

The footer and Help ▸ About link the source code of the running build:
`GET /about` (WebSocket `about.get`) returns `{version, build, license,
source_url, source_ref, source_link}`. `source_ref` is the commit (build id
`<commit> (<date>)`) or release tag (`REQMAN_BUILD=v1.2.3`); a `dev` build,
a build from a checkout with uncommitted changes, or a `-dirty` release
build has no ref and links the repository itself. Help ▸ About ▸
Third-party licences lists the frontend's npm packages (generated by
`npm run build` into `assets/third-party-licenses.json`) and the backend's
Python packages (`GET /about/third-party`, read from the installed
environment).

## 10. Project layout

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

## 11. Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — data model, disk layout,
  backend layering, API surface, frontend structure, how to extend the
  system
- [`docs/BACKLOG.md`](docs/BACKLOG.md) — feature status and a DOORS-derived
  reference checklist for what a mature requirements tool has

## 12. Testing

```sh
cd backend && .venv/bin/pip install -r requirements-dev.txt   # pytest, httpx (once)
cd backend && .venv/bin/python -m pytest -q
cd frontend && npm run build && npm run lint
```

Test-only dependencies are in `backend/requirements-dev.txt` (or the `test`
extra of `backend/pyproject.toml`). GitHub Actions runs the same checks on
every push and pull request (`.github/workflows/ci.yml`).

## 13. Security Considerations

### 13.1. Data Protection

- **At-rest encryption**: The local edition stores data as plain JSON files. For production deployments, **the data directory must reside on an encrypted filesystem** (LUKS on Linux, FileVault on macOS, BitLocker on Windows) to protect requirements data at rest.
- **In-transit encryption**: The server edition enforces HTTPS for all authentication providers (`cookie_secure: true` by default). Configure a valid TLS certificate for production.
- **Secrets management**: OAuth client secrets are read from environment variables (`REQMAN_AUTH_<ID>_CLIENT_SECRET`) or the OS keychain (`keyring` package). They are never written to config files, logs, or sent to the browser.

### 13.2. TLS / HTTPS Configuration

ReqManTool's backend can serve HTTPS directly when configured with TLS certificates. This is useful for production deployments without a reverse proxy, or for development with self-signed certificates.

**Configuration options:**

| Setting | Environment Variable | Config Key | Description |
|---------|---------------------|------------|-------------|
| TLS certificate file | `REQMAN_TLS_CERT_FILE` | `tls_cert_file` | Path to PEM-encoded certificate file |
| TLS private key file | `REQMAN_TLS_KEY_FILE` | `tls_key_file` | Path to PEM-encoded private key file |
| TLS CA certificate file | `REQMAN_TLS_CA_FILE` | `tls_ca_file` | Optional: CA certificate for mTLS (client certificate verification) |

**CLI flags for `reqman serve` and `reqman init`:**

```sh
reqman serve --tls-cert-file /path/to/cert.pem --tls-key-file /path/to/key.pem [--tls-ca-file /path/to/ca.pem]
reqman init --tls-cert-file /path/to/cert.pem --tls-key-file /path/to/key.pem [--tls-ca-file /path/to/ca.pem]
```

**Example config file (`config.json`):**

```json
{
  "format_version": 1,
  "data_dir": "/var/lib/reqman/data",
  "host": "0.0.0.0",
  "port": 8443,
  "cors_origins": ["https://reqman.example.com"],
  "tls_cert_file": "/etc/ssl/certs/reqman.pem",
  "tls_key_file": "/etc/ssl/private/reqman.key",
  "tls_ca_file": "/etc/ssl/certs/ca.pem"
}
```

**Generating a self-signed certificate for development:**

```sh
openssl req -x509 -newkey rsa:2048 -keyout server.key -out server.pem -days 365 -nodes -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost"
```

Then run:

```sh
reqman serve --tls-cert-file server.pem --tls-key-file server.key
```

The backend will serve HTTPS on the configured port. The frontend (when built and served from the same origin) will automatically use `wss://` for WebSocket connections.

**mTLS (mutual TLS):** When `tls_ca_file` is configured, the backend requires clients to present a valid client certificate signed by that CA. This is useful for zero-trust network architectures.

### 13.3. Server Edition Hardening

When running the server edition (`mode: server`), the following security controls are active:

- **Rate limiting**: 10 requests/minute on `/auth/login/` and `/auth/callback/` endpoints (configurable via `RateLimitMiddleware`).
- **Security headers**: HSTS (1 year), CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`.
- **Session limits**: Maximum 5 concurrent sessions per user (configurable via `server.max_sessions_per_user`).
- **Audit logging**: All administrative actions and logins recorded to `audit.jsonl` with automatic rotation (100 MiB / 10 files).
- **CSRF protection**: Synchronizer token pattern (`X-CSRF-Token` header) on all state-changing requests.
- **SAML metadata**: Metadata URLs validated at config time (HTTPS required); no runtime fetch to prevent SSRF.

### 13.4. Configuration Best Practices

- Set `server.cookie_secure: true` in production (default).
- Restrict `cors_origins` to your frontend domain(s).
- Use `server.admins` and `server.admin_groups` to limit server administrator access.
- `server.require_module_checkout` (default `true`): set it to `false` to make the checkout opt-in for exclusive editing workflows.
- Monitor `audit.jsonl` for suspicious activity.

## 20. Status

Actively developed; core requirement/module/link/nesting/export workflows
are implemented and tested. See `docs/BACKLOG.md` for what's next.

## 21. License

Dual-licensed: [AGPL-3.0-only](LICENSE) for the community (local)
edition, or a commercial licence. The server edition is commercial only.
See [LICENSING.md](LICENSING.md). Contributions need a contributor licence
agreement; see [CONTRIBUTING.md](CONTRIBUTING.md).