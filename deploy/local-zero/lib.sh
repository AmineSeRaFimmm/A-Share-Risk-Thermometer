#!/usr/bin/env bash
# Shared helpers for zero-cost local production (TestFlight self-host).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export ROOT
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

# Prefer project venv
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  export PY="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  export PY="$ROOT/venv/bin/python"
else
  export PY="${PY:-python3}"
fi

export LOG_DIR="${LOG_DIR:-$ROOT/deploy/local-zero/logs}"
export LOCK_DIR="${LOCK_DIR:-$ROOT/deploy/local-zero/locks}"
mkdir -p "$LOG_DIR" "$LOCK_DIR"

log() {
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "[$ts] $*" | tee -a "$LOG_DIR/pipeline.log"
}

# Shanghai wall clock: HHMM as integer 930..1500
shanghai_hhmm() {
  TZ=Asia/Shanghai date +%H%M
}

shanghai_dow() {
  # 1=Mon … 7=Sun
  TZ=Asia/Shanghai date +%u
}

# True on Mon–Fri Shanghai (does not know A-share holidays — acceptable for self-use).
is_weekday_sh() {
  local d
  d="$(shanghai_dow)"
  [[ "$d" -ge 1 && "$d" -le 5 ]]
}

# Continuous auction + closing: 09:25–15:05 Shanghai (padded for free-source lag).
is_intraday_window_sh() {
  is_weekday_sh || return 1
  local t
  t="$(shanghai_hhmm)"
  # 0925–1135 morning, 1255–1505 afternoon
  if [[ "$t" -ge 925 && "$t" -le 1135 ]]; then return 0; fi
  if [[ "$t" -ge 1255 && "$t" -le 1505 ]]; then return 0; fi
  return 1
}

# Post-close formal rebuild window: 15:20–18:30 Shanghai weekdays.
is_eod_window_sh() {
  is_weekday_sh || return 1
  local t
  t="$(shanghai_hhmm)"
  [[ "$t" -ge 1520 && "$t" -le 1830 ]]
}

with_lock() {
  local name="$1"
  shift
  local lock="$LOCK_DIR/${name}.lock"
  if [[ -f "$lock" ]]; then
    local old
    old="$(cat "$lock" 2>/dev/null || true)"
    if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
      log "SKIP $name — already running pid=$old"
      return 0
    fi
  fi
  echo $$ >"$lock"
  # shellcheck disable=SC2064
  trap 'rm -f "$lock"' RETURN
  "$@"
}
