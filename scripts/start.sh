#!/usr/bin/env bash
# Start StarIndex on Linux or macOS (normal use). First run: creates the environment and builds the UI.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip --quiet
  .venv/bin/python -m pip install -e . --quiet
fi
if [ ! -f frontend/dist/index.html ]; then
  (cd frontend && npm ci --no-audit --no-fund && npm run build)
fi
exec .venv/bin/python -m starindex.cli serve --open "$@"
