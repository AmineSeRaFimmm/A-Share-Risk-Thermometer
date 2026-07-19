#!/usr/bin/env bash
set -euo pipefail
LAUNCH="$HOME/Library/LaunchAgents"
for label in com.ashare.rt.app-server com.ashare.rt.intraday com.ashare.rt.eod com.ashare.rt.tunnel; do
  launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
  rm -f "$LAUNCH/${label}.plist"
  echo "removed $label"
done
echo "done"
