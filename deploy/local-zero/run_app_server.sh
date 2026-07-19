#!/usr/bin/env bash
# Long-running app data plane (UI + JSON + /api/*). KeepAlive via launchd.
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

PORT="${APP_PORT:-8787}"
HOST="${APP_HOST:-0.0.0.0}"

cd "$ROOT"
log "app_server: starting on ${HOST}:${PORT} (auto-refresh=none — schedule owns updates)"
exec "$PY" scripts/app_server.py \
  --host "$HOST" \
  --port "$PORT" \
  --auto-refresh none \
  >>"$LOG_DIR/app_server.log" 2>&1
