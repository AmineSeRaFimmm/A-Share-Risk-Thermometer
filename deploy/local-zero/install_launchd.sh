#!/usr/bin/env bash
# Install macOS launchd jobs for zero-cost self-host (TestFlight data plane).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ZERO="$ROOT/deploy/local-zero"
LAUNCH="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH" "$ZERO/logs" "$ZERO/locks"
chmod +x "$ZERO"/*.sh

# Rewrite plists with absolute paths for this machine
render() {
  local src="$1"
  local dest="$2"
  sed \
    -e "s|__ROOT__|${ROOT}|g" \
    -e "s|__HOME__|${HOME}|g" \
    -e "s|__PY__|$(command -v python3)|g" \
    "$src" >"$dest"
}

render "$ZERO/launchd/com.ashare.rt.app-server.plist" \
  "$LAUNCH/com.ashare.rt.app-server.plist"
render "$ZERO/launchd/com.ashare.rt.intraday.plist" \
  "$LAUNCH/com.ashare.rt.intraday.plist"
render "$ZERO/launchd/com.ashare.rt.eod.plist" \
  "$LAUNCH/com.ashare.rt.eod.plist"

# Optional tunnel (only if conf exists and cloudflared installed)
if [[ -f "$ZERO/cloudflared/config.yml" ]] && command -v cloudflared >/dev/null 2>&1; then
  render "$ZERO/launchd/com.ashare.rt.tunnel.plist" \
    "$LAUNCH/com.ashare.rt.tunnel.plist"
  launchctl bootout "gui/$(id -u)/com.ashare.rt.tunnel" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$LAUNCH/com.ashare.rt.tunnel.plist"
  launchctl enable "gui/$(id -u)/com.ashare.rt.tunnel"
  launchctl kickstart -k "gui/$(id -u)/com.ashare.rt.tunnel"
  echo "→ tunnel agent installed"
else
  echo "→ tunnel skipped (install cloudflared + write cloudflared/config.yml first)"
fi

for label in com.ashare.rt.app-server com.ashare.rt.intraday com.ashare.rt.eod; do
  launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$LAUNCH/${label}.plist"
  launchctl enable "gui/$(id -u)/${label}"
  launchctl kickstart -k "gui/$(id -u)/${label}" 2>/dev/null || true
  echo "→ installed $label"
done

echo
echo "Installed. Check:"
echo "  launchctl print gui/$(id -u)/com.ashare.rt.app-server | head"
echo "  curl -s http://127.0.0.1:8787/api/status | head"
echo "  tail -f $ZERO/logs/pipeline.log"
echo
echo "Mac 时区建议设为 Asia/Shanghai，或接受 launchd 用本机时区触发、脚本内用上海时间判窗。"
