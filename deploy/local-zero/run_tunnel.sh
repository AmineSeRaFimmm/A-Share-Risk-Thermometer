#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CFG="$ROOT/deploy/local-zero/cloudflared/config.yml"
LOG="$ROOT/deploy/local-zero/logs"

if [[ ! -f "$CFG" ]]; then
  echo "missing $CFG — copy config.example.yml and fill tunnel id" >&2
  exit 1
fi

CF=""
for c in /opt/homebrew/bin/cloudflared /usr/local/bin/cloudflared "$(command -v cloudflared 2>/dev/null || true)"; do
  if [[ -n "$c" && -x "$c" ]]; then CF="$c"; break; fi
done
if [[ -z "$CF" ]]; then
  echo "cloudflared not found — brew install cloudflared" >&2
  exit 1
fi

mkdir -p "$LOG"
exec "$CF" tunnel --config "$CFG" run >>"$LOG/tunnel.out" 2>>"$LOG/tunnel.err"
