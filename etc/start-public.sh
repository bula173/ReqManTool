#!/usr/bin/env bash
# Run ReqManTool behind a single origin for a reverse proxy / Cloudflare
# Tunnel: backend on localhost:8899, built frontend on localhost:4173 with
# API and WebSocket calls proxied to the backend. See docs/CLOUDFLARE.md.
#
# Usage: etc/start-public.sh <public-host> [backend args, e.g. --dir PATH]
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 <public-host> [backend args]" >&2
    exit 2
fi
PUBLIC_HOST="$1"
shift

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="$ROOT/frontend"

"$ROOT/etc/start-backend.sh" "$@" &
BACKEND_PID=$!

cleanup() {
    kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT

cd "$FRONTEND"

if [ ! -d node_modules ]; then
    npm install
fi

# Empty API base = same origin; the WebSocket goes through the public host.
VITE_API_BASE_URL="" VITE_WS_URL="wss://$PUBLIC_HOST/ws" npm run build
npm run preview -- --host 127.0.0.1 --port 4173 --strictPort
