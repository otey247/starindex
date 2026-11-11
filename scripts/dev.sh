#!/usr/bin/env bash
# Development mode: backend on :8765 and the Vite dev server on :5173 with a throwaway data directory.
set -euo pipefail
cd "$(dirname "$0")/.."
export STARINDEX_DATA_DIR="${STARINDEX_DATA_DIR:-$PWD/.dev-data}"
export STARINDEX_LOG_LEVEL="${STARINDEX_LOG_LEVEL:-DEBUG}"
.venv/bin/python -m starindex.cli serve &
backend=$!
trap 'kill $backend 2>/dev/null || true' EXIT
cd frontend
[ -d node_modules ] || npm ci
npm run dev
