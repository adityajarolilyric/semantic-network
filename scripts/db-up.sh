#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f backend/.env ]]; then
  exec docker compose --env-file backend/.env -f backend/compose.dev.yml up -d --wait
fi
exec docker compose -f backend/compose.dev.yml up -d --wait
