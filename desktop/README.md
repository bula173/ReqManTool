# ReqManTool Desktop App - Development Guide

## Overview

The desktop app bundles the Python backend and serves the frontend in a native Tauri window. It provides one-click installers for macOS (DMG), Windows (MSI), and Linux (AppImage).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Tauri Application                        │
│  ┌─────────────────┐    ┌────────────────────────────────┐  │
│  │   Rust Frontend │◄───│  WebView (Frontend UI)         │  │
│  │  (Window mgmt)  │    │  (React + Mantine)             │  │
│  └────────┬────────┘    └──────────────┬─────────────────┘  │
│           │                              │                   │
│           │         ┌────────────────────┴────────────┐     │
│           └────────►│     Python Backend (reqman)     │     │
│                     │  - REST API (port auto-picked)  │     │
│                     │  - WebSocket                    │     │
│                     │  - Git integration              │     │
│                     └─────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

## Development Setup

### Prerequisites

- **Rust**: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- **Node.js**: >= 20 (LTS)
- **Python**: >= 3.11
- **Tauri CLI**: `cargo install tauri-cli --locked`
- **System dependencies**:
  - **Linux**: `libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libgtk-3-dev`
  - **macOS**: Xcode Command Line Tools
  - **Windows**: WebView2 Runtime (auto-installed)

### Quick Start

```bash
# From repo root - one command: bundles the Python backend, starts the dev
# backend (:8899) and Vite (:5173) if they are not running, then `tauri dev`
./etc/start-desktop.sh

# Build the installers instead
./etc/start-desktop.sh --build
```

Or by hand:

```bash
cd desktop

# Install dependencies
npm install

# Development mode (needs the Vite dev server on :5173 in another terminal:
# ./etc/start-frontend.sh, and a backend on :8899: ./etc/start-backend.sh)
npm run dev

# Or with explicit Tauri command
cargo tauri dev
```

### Building

```bash
# Production build (creates installers)
npm run build

# Or
cargo tauri build
```

Outputs:
- `src-tauri/target/release/bundle/dmg/ReqManTool_*.dmg` (macOS)
- `src-tauri/target/release/bundle/msi/ReqManTool_*.msi` (Windows)
- `src-tauri/target/release/bundle/appimage/ReqManTool_*.AppImage` (Linux)

## Configuration

### Tauri Config (`src-tauri/tauri.conf.json`)

Key settings:
- `build.frontendDist`: Points to `../../frontend/dist` (the built frontend in `frontend/`)
- `build.devUrl`: The Vite dev server (`http://localhost:5173`) used by `tauri dev`
- `bundle.resources`: Includes `backend/` (Python backend; refresh it with
  `etc/start-desktop.sh`, which does it for you)
- `bundle.macOS.signingIdentity`: `-` (ad-hoc) so local builds run unsigned;
  set a Developer ID identity (or leave it to CI secrets) for distribution
- `plugins.updater`: GitHub Releases endpoint for auto-update

### Icons

Place source SVG at `src-tauri/icons/icon.svg` and run:
```bash
cd src-tauri/icons
./generate-icons.sh
```
Or use `tauri-icon` (recommended):
```bash
cargo install tauri-icon
tauri-icon -i icons/icon.svg -o icons
```

## Backend Bundling

The Python backend is copied into `src-tauri/backend/` and shipped as a
Tauri resource (`bundle.resources`), so it sits next to the executable at
runtime. `etc/start-desktop.sh` (and the release workflow) do the copying:

1. Copies `backend/reqman/`, `backend/reqman_server/`, `requirements.txt`,
   `pyproject.toml` and `LICENSE` to `src-tauri/backend/`, plus an
   `__main__.py` entry point (`reqman/middleware.py` needs `reqman_server`)
2. In development the resource lands in `src-tauri/target/debug/backend/`,
   in a bundle inside the `.app` / install dir
3. Runs `python3 -m reqman serve` on a random free port with
   `--cors-origins http://localhost:5173,tauri://localhost,http://tauri.localhost`
   (dev server, macOS bundle origin, Windows/Linux bundle origin), so the
   system Python must have the runtime dependencies
   (`pip install -r backend/requirements.txt`); on Windows an existing
   `backend/.venv/Scripts/python.exe` is preferred when present
4. The backend address is handed to the window only after `/edition` answers
   `{"mode": "local"}` — publishing it earlier races the webview's first
   request and fails it

### Startup Modes

The window starts with `visible: false` and is shown only when the startup
state below is resolved, so nothing flashes half-loaded.

**Local instance** (default) — Rust starts the bundled backend on a random
free port and the webview is pointed at it through `API_BASE_URL`.

**Server** — no local backend is started at all; the window just loads the
server address the user typed. That server serves its own UI and API, so
there is nothing to proxy in between.

How the mode is picked:

1. A release build with no `app_mode` in the config asks with a native
   Yes/No dialog: *Yes* = local instance, *No* = the start screen (below).
   A debug build (`cargo tauri dev`) never asks — it always runs local and
   persists `app_mode: "local"` so the start screen stays out of the way.
2. The **footer** (left side, `frontend/src/components/VersionFooter.tsx`)
   shows the current choice — *Local instance*, or the host of the server —
   and opens a menu with **Local instance** / **Server address…**. The latter
   clears `app_mode`, so the start screen reappears with the last URL
   pre-filled. The start screen itself
   (`frontend/src/components/StartScreen.tsx`) offers the same two choices.
   The system tray only keeps *Show ReqManTool* and *Quit*: in server mode the
   window shows the server's own page, where this footer may not exist, so
   edit `app_mode` in the config to switch back.

### Shared Config File

`~/Library/Application Support/ReqManTool/config.json` (per-platform app data
dir) is written by the Rust side **and read by the backend**
(`reqman/config.py`), so the two must agree:

| Key | Meaning |
| --- | --- |
| `app_mode` | `local` / `server` / absent (ask). Our key — never write `mode`, the backend validates that one itself |
| `server_url` | Last address typed on the start screen |
| `data_dir` | Shared with the backend: where the requirements live |
| `last_port`, `window_state`, `auto_update`, … | Desktop-only |

Every desktop-only field is `Option` and skipped when `null`
(`skip_serializing_if`), because the backend rejects `mode: null` with
*mode must be one of local, server, got None*. Unknown keys are tolerated,
so the backend's own keys (`mode`, `server`, …) can live in the same file.

### Logs and PID

The bundled backend logs to `backend.log` next to the config file, and its
PID (plus start time, for identity checks) to `backend.pid`. On shutdown the
Rust side reaps that child — on Windows with `taskkill /F /T` — and removes
the PID file only when it actually killed something, so a leftover file from
a crash is cleaned up on the next start without killing an unrelated process.

### First Run Flow

1. App starts → reads the config (mode, `data_dir`, window state)
2. Mode resolved as described above; the start screen shows if it is unknown
3. If no `data_dir` configured → shows folder picker dialog
4. Starts backend on random port (e.g., `http://127.0.0.1:12345`) and waits
   for `/edition` to answer `{"mode": "local"}`
5. Shows the main window; in development it loads the Vite dev server
   (`devUrl`, which proxies API calls to the backend on `:8899`), in a
   bundle it serves the embedded `frontendDist`

## Auto-Update

Updates are delivered via GitHub Releases:
- Endpoint: `https://github.com/bula173/ReqManTool/releases/latest/download/latest.json`
- Public key configured in `tauri.conf.json` (generate with `tauri-signer`)
- Checks on startup (configurable)
- Background download, prompt to install on next restart

## Git Integration

The app checks for system `git` on startup:
- If found: full git history features enabled
- If not found: history features disabled with warning

## Troubleshooting

### Backend fails to start
- Check Python version: `python3 --version` (needs 3.11+)
- Check dependencies: `pip install -r backend/requirements.txt`
- Check port availability (firewall/antivirus)

### Frontend doesn't load
- Ensure `npm run build` completed in `frontend/`
- Check `src-tauri/tauri.conf.json` `build.frontendDist` path (and that `npm run build` ran in `frontend/`)

### "The backend does not answer" / UI keeps loading
- The startup state publishes the backend URL only after `/edition` replies;
  if it never does, `backend.log` (next to the config file) has the reason —
  usually a Python without the runtime dependencies
- `python3 -m reqman serve` needs `pip install -r backend/requirements.txt`

### Live updates never connect (WebSocket 404)
`uvicorn` needs a WebSocket implementation to serve `/ws`. When it is
missing the backend logs

```
WARNING:  No supported WebSocket library detected.
```

and `/ws` answers 404 (the app keeps running, it just does not refresh live).
Fix it for the Python that runs the backend:

```bash
python3 -m pip install websockets   # or: pip install 'uvicorn[standard]'
```

The dev stack does not hit this because `etc/start.sh` installs a venv that
already contains `websockets`.

### "Command not found: tauri"
```bash
cargo install tauri-cli --locked
```

### macOS: "App is damaged"
```bash
xattr -cr /Applications/ReqManTool.app
codesign --force --deep --sign - /Applications/ReqManTool.app
```

### Windows: WebView2 missing
The installer should auto-install WebView2. If not:
```powershell
winget install Microsoft.EdgeWebView2Runtime
```

## CI/CD

`.github/workflows/desktop.yml` runs on pushes to `main`, on `v*` tags, on
pull requests and on `workflow_dispatch`, with three jobs:

**build** (matrix, `fail-fast: false` so one platform cannot discard the
others' packages; `shell: bash` for every step because "Prepare Python
backend" is POSIX shell and Windows runners default to PowerShell):

| Runner | Target | Package |
| --- | --- | --- |
| `macos-latest` | `universal-apple-darwin` (built as `aarch64` + `x86_64`, merged) | DMG |
| `windows-latest` | `x86_64-pc-windows-msvc` | MSI |
| `ubuntu-latest` | `x86_64-unknown-linux-gnu` | AppImage |

Steps: checkout (`fetch-depth: 0` for version info) → Rust (matrix targets)
→ Node 20 with npm cache → Python 3.11 → Linux GTK/WebKit deps →
`npm ci` in `frontend/` and `desktop/` → `npm run build` (frontend) →
bundle the backend → `tauri-apps/tauri-action` → upload the package.

**test** (`needs: build`, per platform, `fail-fast: false`) — a smoke test
that the package actually installs and runs:

- macOS: `hdiutil attach` the DMG, run the app binary with `--version`,
  detach
- Windows: silent MSI install (`msiexec /i … /qn`), find the install
  location from the `Uninstall` registry entries instead of guessing a path,
  run `ReqManTool.exe --version`, silent uninstall
- Linux: `APPIMAGE_EXTRACT_AND_RUN=1` (runners have no usable `/dev/fuse`)
  and `./ReqManTool.AppImage --version`

`--version` is handled before anything else in `main()`, so the smoke test
cannot hang on a GUI process with no console (and it never starts a backend).

**release** (`needs: [build, test]`, only on a `v*` tag) — downloads every
package, writes `SHA256SUMS.txt`, and attaches the packages plus checksums
to the GitHub release with `softprops/action-gh-release@v2`.

### Release Process

```bash
# Tag and push
git tag v1.0.0
git push origin v1.0.0

# GitHub Actions builds, smoke-tests and publishes the release automatically
```

## File Structure

```
desktop/
├── package.json              # Node dependencies, scripts
├── README.md                 # This file
└── src-tauri/
    ├── Cargo.toml            # Rust dependencies
    ├── tauri.conf.json       # Tauri configuration
    ├── build.rs              # Build script (if needed)
    ├── src/
    │   └── main.rs           # Rust entry point
    ├── icons/
    │   ├── icon.svg          # Source icon
    │   ├── generate-icons.sh # Icon generation script
    │   └── *.png/*.ico/*.icns # Generated icons
    ├── backend/              # Bundled Python backend (populated at build)
    │   ├── reqman/
    │   ├── templates/
    │   ├── prompts/
    │   ├── requirements.txt
    │   ├── pyproject.toml
    │   └── __main__.py
    └── target/               # Build output (gitignored)
        └── release/
            └── bundle/
                ├── dmg/      # macOS installer
                ├── msi/      # Windows installer
                └── appimage/ # Linux AppImage
```

## Testing

```bash
# Run Rust tests
cargo test

# Test backend startup
cd src-tauri/backend && python -m reqman serve --dir /tmp/test --port 0

# Test frontend build
cd ../../.. && npm run build
```

## License

AGPL-3.0-only. See `../LICENSE` for details.