#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/setup-backend.sh
npm ci
npm ci --prefix application
