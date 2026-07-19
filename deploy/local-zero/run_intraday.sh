#!/usr/bin/env bash
# Trading-session realtime refresh (nowcast). No-op outside market window.
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

run_intraday() {
  if ! is_intraday_window_sh; then
    log "intraday: outside window (SH $(TZ=Asia/Shanghai date '+%F %H:%M') dow=$(shanghai_dow)) — skip"
    return 0
  fi
  log "intraday: start realtime AVIX + site mirror"
  cd "$ROOT"
  if ! "$PY" scripts/update_realtime_avix.py >>"$LOG_DIR/intraday.log" 2>&1; then
    log "intraday: update_realtime_avix FAILED (see intraday.log)"
    return 1
  fi
  # Ensure docs/ web+data mirror for app_server
  "$PY" - <<'PY' >>"$LOG_DIR/intraday.log" 2>&1 || true
import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
from scripts.app_server import _sync_web_to_docs, _sync_site_data_to_docs
_sync_web_to_docs()
_sync_site_data_to_docs()
print("sync ok")
PY
  log "intraday: ok"
}

with_lock intraday run_intraday
