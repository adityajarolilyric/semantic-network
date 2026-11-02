#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x backend/.venv/bin/python ]]; then
  echo "Run npm run setup to install the backend and UI dependencies." >&2
  exit 1
fi
exec backend/.venv/bin/python -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8788 --reload --reload-dir backend/app
