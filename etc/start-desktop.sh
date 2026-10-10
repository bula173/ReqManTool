#!/usr/bin/env bash
# Build and/or run the ReqManTool desktop app (Tauri) from a git checkout.
#
# Usage: etc/start-desktop.sh [--build] [tauri args...]
#   (default)   development run: bundle the Python backend, start the dev
#               backend (:8899) and the Vite frontend (:5173) if they are not
#               already running, then `tauri dev` (native window, hot reload).
#               The window loads the Vite dev server, which proxies the API to
#               the backend, so both halves must be up; anything this script
#               started itself is stopped again when it exits.
#   --build     build the installers with `tauri build`: `vite build` in
#               frontend/, bundle backend + frontend, release compile.
#               Artefacts land in
#               desktop/src-tauri/target/release/bundle/{dmg,msi,appimage}/
#   -h, --help  this text.
# Any other argument is passed through to `tauri dev` / `tauri build`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP="$ROOT/desktop"
TAURI="$DESKTOP/src-tauri"
LOGDIR="$DESKTOP/.logs"

log() { printf '[start-desktop] %s\n' "$*"; }
die() { printf '[start-desktop] %s\n' "$*" >&2; exit 1; }

MODE=run
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --build) MODE=build ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) ARGS+=("$arg") ;;
    esac
done

for cmd in cargo node npm python3; do
    command -v "$cmd" >/dev/null 2>&1 ||
        die "'$cmd' not found. $(case "$cmd" in
            cargo) echo 'Install Rust: https://rustup.rs' ;;
            node|npm) echo 'Install Node.js >= 20: https://nodejs.org' ;;
            python3) echo 'Install Python >= 3.11: https://python.org' ;;
        esac)"
done

[ -d "$DESKTOP" ] || die "desktop project not found at $DESKTOP"

if [ ! -d "$DESKTOP/node_modules" ]; then
    log "installing desktop dependencies"
    (cd "$DESKTOP" && npm install)
fi

# The Rust side runs `python -m reqman serve` from the bundled backend copy
# (desktop/src-tauri/backend), which only CI used to create. Refresh it from
# the checkout so dev runs and bundles always see the current backend. Both
# packages are needed: reqman/middleware.py imports reqman_server.settings.
bundle_backend() {
    log "bundling the Python backend into desktop/src-tauri/backend"
    local stage pkg
    stage="$(mktemp -d)"
    for pkg in reqman reqman_server; do
        if command -v rsync >/dev/null 2>&1; then
            rsync -a --exclude '__pycache__' --exclude '*.pyc' \
                "$ROOT/backend/$pkg" "$stage/"
        else
            cp -R "$ROOT/backend/$pkg" "$stage/$pkg"
            find "$stage/$pkg" -name '__pycache__' -type d -prune -exec rm -rf {} +
        fi
    done
    cp "$ROOT/backend/requirements.txt" "$ROOT/backend/pyproject.toml" \
        "$ROOT/LICENSE" "$stage/"
    cat > "$stage/__main__.py" <<'EOF'
"""python -m reqman runs the CLI."""
import sys
from reqman.cli import main
sys.exit(main())
EOF
    rm -rf "$TAURI/backend"
    mv "$stage" "$TAURI/backend"
}

# `localhost` (not 127.0.0.1): Vite binds the IPv6 loopback, and tauri's
# devUrl is http://localhost:5173, so probe the same name it uses.
up() { curl -fsS --max-time 2 -o /dev/null "$1" 2>/dev/null; }

wait_for() { # url tries
    local i
    for ((i = 0; i < $2; i++)); do
        up "$1" && return 0
        sleep 1
    done
    return 1
}

STARTED=()
cleanup() {
    local pid
    for pid in ${STARTED[@]+"${STARTED[@]}"}; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

if [ "$MODE" = build ]; then
    log "building the frontend"
    if [ ! -d "$ROOT/frontend/node_modules" ]; then
        (cd "$ROOT/frontend" && npm install)
    fi
    (cd "$ROOT/frontend" && npm run build)

    bundle_backend

    log "building the desktop app (this compiles a release binary)"
    (cd "$DESKTOP" && npm run build -- ${ARGS[@]+"${ARGS[@]}"})
    exit 0
fi

mkdir -p "$LOGDIR"
bundle_backend

if ! up http://localhost:8899/edition; then
    log "starting the dev backend on :8899 (log: $LOGDIR/backend.log)"
    "$ROOT/etc/start-backend.sh" >"$LOGDIR/backend.log" 2>&1 &
    STARTED+=("$!")
    wait_for http://localhost:8899/edition 60 ||
        die "backend did not come up, see $LOGDIR/backend.log"
else
    log "reusing the backend already running on :8899"
fi

if ! up http://localhost:5173/; then
    log "starting the Vite dev frontend on :5173 (log: $LOGDIR/frontend.log)"
    "$ROOT/etc/start-frontend.sh" >"$LOGDIR/frontend.log" 2>&1 &
    STARTED+=("$!")
    wait_for http://localhost:5173/ 60 ||
        die "frontend did not come up, see $LOGDIR/frontend.log"
else
    log "reusing the frontend already running on :5173"
fi

log "starting tauri dev (first build takes a few minutes)"
cd "$DESKTOP"
npm run dev -- ${ARGS[@]+"${ARGS[@]}"}
