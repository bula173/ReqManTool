#!/usr/bin/env bash
# Start ReqManTool from a git checkout: backend + Vite dev frontend.
#
# Usage: etc/start.sh [--local | --desktop | --server] [backend args...]
#   --local, --desktop  local edition: no login (REQMAN_MODE=local)
#   --server            server edition: login required (REQMAN_MODE=server;
#                       needs the server settings, see docs/ARCHITECTURE.md)
#   (no flag)           mode from REQMAN_MODE / the config file / default local
# Any other arguments go to `reqman serve`, e.g. --dir ~/ReqManData --port 8899.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Local secrets (e.g. REQMAN_AUTH_GITHUB_CLIENT_SECRET) live in the
# git-ignored .reqman/server.env as KEY=value lines.
if [ -f "$ROOT/.reqman/server.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.reqman/server.env"
    set +a
fi

BACKEND_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --local|--desktop) export REQMAN_MODE=local ;;
        --server) export REQMAN_MODE=server ;;
        -h|--help)
            sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) BACKEND_ARGS+=("$arg") ;;
    esac
done

if [ -n "${REQMAN_MODE:-}" ]; then
    echo "ReqManTool mode: $REQMAN_MODE"
fi

"$ROOT/etc/start-backend.sh" ${BACKEND_ARGS[@]+"${BACKEND_ARGS[@]}"} &
BACKEND_PID=$!

cleanup() {
    kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT

sleep 1
"$ROOT/etc/start-frontend.sh"
