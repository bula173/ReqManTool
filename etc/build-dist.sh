#!/usr/bin/env bash
# Build the release artefacts: the frontend bundled into the Python package,
# then a wheel and an sdist in <repo>/dist (or the directory given).
#
# Usage: etc/build-dist.sh [OUTDIR]
#
# Needs node/npm and a Python with the `build` module (pip install build).
# PYTHON selects the interpreter (default python3). The copies made into
# backend/ (reqman/static, reqman/BUILD, LICENSE) are removed again afterwards
# (and gitignored), so a checkout's dev backend keeps serving the API only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTDIR="${1:-$ROOT/dist}"
PYTHON="${PYTHON:-python3}"

# One build id for the frontend footer and the backend (GET /version):
# REQMAN_BUILD if set (CI: the release tag), else "<short commit> (<date>)".
if [ -z "${REQMAN_BUILD:-}" ]; then
    COMMIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || true)"
    REQMAN_BUILD="${COMMIT:+$COMMIT ($(date +%Y-%m-%d))}"
    REQMAN_BUILD="${REQMAN_BUILD:-dev}"
fi
export REQMAN_BUILD

cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
    npm ci
fi
# No VITE_* overrides: a production build talks to the page origin, i.e. the
# backend that serves it.
npm run build

STATIC="$ROOT/backend/reqman/static"
cleanup() {
    rm -rf "$STATIC" "$ROOT/backend/reqman/BUILD" "$ROOT/backend/LICENSE" \
        "$ROOT/backend/build" "$ROOT/backend/reqmantool.egg-info"
}
trap cleanup EXIT
cleanup
cp -R "$ROOT/frontend/dist" "$STATIC"
cp "$ROOT/LICENSE" "$ROOT/backend/LICENSE"
printf '%s\n' "$REQMAN_BUILD" > "$ROOT/backend/reqman/BUILD"

"$PYTHON" -m build --outdir "$OUTDIR" "$ROOT/backend"
echo "Built into $OUTDIR:"
ls -1 "$OUTDIR"
