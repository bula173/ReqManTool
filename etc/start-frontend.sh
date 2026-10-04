#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="$ROOT/frontend"

cd "$FRONTEND"

if [ ! -d node_modules ]; then
    npm install
fi

exec npm run dev -- "$@"
