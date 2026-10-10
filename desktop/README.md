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
   `--cors-origins http://localhost:5173,tauri://localhost`, so the system
   Python must have the runtime dependencies
   (`pip install -r backend/requirements.txt`); on Windows an existing
   `backend/.venv/Scripts/python.exe` is preferred when present

### First Run Flow

1. App starts → checks for config
2. If no `data_dir` configured → shows folder picker dialog
3. Starts backend on random port (e.g., `http://127.0.0.1:12345`)
4. Waits for `/edition` endpoint to return `{"mode": "local"}`
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

GitHub Actions workflow (`.github/workflows/desktop.yml`) builds on every tag push:
1. Sets up Rust, Node.js, Python
2. Builds frontend
3. Bundles backend
4. Runs `tauri build` for each platform
5. Uploads artifacts and creates release

### Release Process

```bash
# Tag and push
git tag v1.0.0
git push origin v1.0.0

# GitHub Actions builds and publishes release automatically
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