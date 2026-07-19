#!/usr/bin/env bash
# Post-close official temperature rebuild. Safe to run multiple times in EOD window.
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

run_eod() {
  if ! is_eod_window_sh; then
    log "eod: outside window (SH $(TZ=Asia/Shanghai date '+%F %H:%M')) — skip"
    return 0
  fi
  log "eod: start update_daily + build_site_data"
  cd "$ROOT"
  if ! "$PY" scripts/update_daily.py >>"$LOG_DIR/eod.log" 2>&1; then
    log "eod: update_daily FAILED (see eod.log)"
    return 1
  fi
  if ! "$PY" scripts/build_site_data.py >>"$LOG_DIR/eod.log" 2>&1; then
    log "eod: build_site_data FAILED (see eod.log)"
    return 1
  fi
  log "eod: ok — official close pipeline finished"
}

with_lock eod run_eod
