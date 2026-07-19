#!/usr/bin/env bash
# Start independent app data plane + Expo (no GitHub dependency).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${APP_PORT:-8787}"
AUTO_REFRESH="${APP_AUTO_REFRESH:-realtime}"

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
if [[ -z "${LAN_IP}" ]]; then
  LAN_IP="127.0.0.1"
fi

export EXPO_PUBLIC_WEB_URL="${EXPO_PUBLIC_WEB_URL:-http://${LAN_IP}:${PORT}}"
# Explicitly do not use GitHub unless user sets this.
export EXPO_PUBLIC_ALLOW_GITHUB="${EXPO_PUBLIC_ALLOW_GITHUB:-0}"
# Critical: without this, Expo manifest points launchAsset to 127.0.0.1
# and Expo Go on phone fails with "failed to parse manifest" / bundle load errors.
export REACT_NATIVE_PACKAGER_HOSTNAME="${REACT_NATIVE_PACKAGER_HOSTNAME:-$LAN_IP}"
export EXPO_DEVTOOLS_LISTEN_ADDRESS="${EXPO_DEVTOOLS_LISTEN_ADDRESS:-0.0.0.0}"

# Persist for Metro / Expo Go
cat > "$ROOT/mobile/.env" <<EOF
EXPO_PUBLIC_WEB_URL=${EXPO_PUBLIC_WEB_URL}
EXPO_PUBLIC_ALLOW_GITHUB=${EXPO_PUBLIC_ALLOW_GITHUB}
REACT_NATIVE_PACKAGER_HOSTNAME=${REACT_NATIVE_PACKAGER_HOSTNAME}
EOF

# Free stale listeners on app port
if command -v lsof >/dev/null 2>&1; then
  for pid in $(lsof -ti tcp:"${PORT}" 2>/dev/null || true); do
    kill "$pid" 2>/dev/null || true
  done
fi

echo "→ App data plane (independent of GitHub)"
echo "  URL:  ${EXPO_PUBLIC_WEB_URL}"
echo "  auto: ${AUTO_REFRESH}"
echo "  API:  ${EXPO_PUBLIC_WEB_URL}api/status"
echo

# Prefer venv python
PY="python3"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
fi

"$PY" "$ROOT/scripts/app_server.py" --host 0.0.0.0 --port "$PORT" --auto-refresh "$AUTO_REFRESH" &
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait until /api/health responds
for i in $(seq 1 90); do
  if curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

curl -s "http://127.0.0.1:${PORT}/api/status" | "$PY" -c "
import sys,json
s=json.load(sys.stdin)
L=s.get('latest') or {}
print(f\"→ RT {L.get('risk_temperature')} · {L.get('temperature_mode')} · {L.get('update_time')}\")
print(f\"  independent_of_github={s.get('independent_of_github')}\")
" || true

cd "$ROOT/mobile"
echo "→ Expo Go URL: exp://${LAN_IP}:8081"
echo "  (manifest launchAsset host must be ${LAN_IP}, not 127.0.0.1)"
npx expo start --host lan --port 8081 "$@"
