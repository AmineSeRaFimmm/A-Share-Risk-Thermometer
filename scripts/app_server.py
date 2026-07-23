#!/usr/bin/env python3
"""
App-native data plane for A-Share Risk Thermometer (Expo / local).

- Serves magazine UI + JSON from THIS machine only.
- Does NOT depend on GitHub Pages for data.
- Can refresh from free market sources via the existing Python pipeline.

Endpoints:
  GET  /api/status          pipeline provenance & freshness
  POST /api/refresh         mode=rebuild|realtime|full
  GET  /api/health          liveness
  *    /*                   static docs/ (UI + data/)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.storage.json_store import dumps_json  # noqa: E402
from src.storage.paths import CALCULATED, DOCS, SITE, WEB, ensure_dirs  # noqa: E402

STATE_LOCK = threading.Lock()
STATE: dict[str, Any] = {
    "refresh_running": False,
    "last_refresh": None,
    "last_error": None,
    "last_mode": None,
    "started_at": None,
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sync_web_to_docs() -> None:
    ensure_dirs()
    DOCS.mkdir(parents=True, exist_ok=True)
    for path in WEB.rglob("*"):
        if path.is_dir():
            continue
        if path.name.endswith(".bak") or "magazine-skin" in path.name:
            continue
        rel = path.relative_to(WEB)
        dest = DOCS / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def _sync_site_data_to_docs() -> None:
    """Canonical data lives in data/site; docs/data is the app-facing mirror."""
    ensure_dirs()
    dest = DOCS / "data"
    dest.mkdir(parents=True, exist_ok=True)
    if SITE.exists():
        for path in SITE.rglob("*"):
            if path.is_dir():
                continue
            rel = path.relative_to(SITE)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    downloads_src = CALCULATED
    downloads_dest = dest / "downloads"
    downloads_dest.mkdir(parents=True, exist_ok=True)
    for name in (
        "risk_temperature.csv",
        "risk_temperature_nowcast.csv",
        "avix_clean_close.csv",
        "qvix_validation.csv",
        "strategy_s3_s4.csv",
        "sector_correlation_metrics.csv",
        "low_position_sector_metrics.csv",
    ):
        src = downloads_src / name
        if src.exists():
            shutil.copy2(src, downloads_dest / name)


def _python_bin() -> str:
    """Prefer project venv so pipeline deps (akshare/pandas) resolve without GitHub."""
    for candidate in (
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".venv" / "bin" / "python3",
        ROOT / "venv" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _run_script(script: str, timeout: int = 900) -> dict[str, Any]:
    cmd = [_python_bin(), str(ROOT / "scripts" / script)]
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "NO_PROXY": os.environ.get("NO_PROXY", "*")},
    )
    return {
        "script": script,
        "returncode": proc.returncode,
        "seconds": round(time.time() - started, 2),
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-4000:],
        "ok": proc.returncode == 0,
    }


def refresh_pipeline(mode: str = "realtime") -> dict[str, Any]:
    """Run local market pipeline. Modes: rebuild | realtime | full."""
    mode = (mode or "realtime").strip().lower()
    if mode not in {"rebuild", "realtime", "full"}:
        mode = "realtime"

    with STATE_LOCK:
        if STATE["refresh_running"]:
            return {"ok": False, "error": "refresh_already_running", "mode": mode}
        STATE["refresh_running"] = True
        STATE["last_mode"] = mode
        STATE["last_error"] = None

    steps: list[dict[str, Any]] = []
    try:
        _sync_web_to_docs()
        if mode == "rebuild":
            steps.append(_run_script("build_site_data.py", timeout=600))
        elif mode == "realtime":
            # Prefer realtime AVIX nowcast path; fall back to full site rebuild.
            try:
                steps.append(_run_script("update_realtime_avix.py", timeout=300))
            except subprocess.TimeoutExpired:
                steps.append({"script": "update_realtime_avix.py", "ok": False, "error": "timeout"})
            if not steps[-1].get("ok"):
                steps.append(_run_script("build_site_data.py", timeout=600))
            else:
                # Ensure docs mirror even if realtime builder only patched SITE.
                _sync_site_data_to_docs()
                # Rebuild full site if latest still missing official files.
                if not (SITE / "history.json").exists() or not (DOCS / "data" / "history.json").exists():
                    steps.append(_run_script("build_site_data.py", timeout=600))
        else:  # full daily pipeline
            steps.append(_run_script("update_daily.py", timeout=1800))
            if steps[-1].get("ok"):
                steps.append(_run_script("build_site_data.py", timeout=600))
            else:
                # Still try to rebuild from whatever cache exists.
                steps.append(_run_script("build_site_data.py", timeout=600))

        _sync_web_to_docs()
        _sync_site_data_to_docs()
        ok = all(s.get("ok") for s in steps if "ok" in s) if steps else False
        # rebuild-only of build_site_data is enough
        if mode == "rebuild":
            ok = bool(steps and steps[-1].get("ok"))
        elif mode == "realtime":
            ok = any(s.get("ok") for s in steps)
        result = {
            "ok": ok,
            "mode": mode,
            "finished_at": _now_iso(),
            "steps": [
                {
                    "script": s.get("script"),
                    "ok": s.get("ok"),
                    "seconds": s.get("seconds"),
                    "returncode": s.get("returncode"),
                    "error": s.get("error"),
                }
                for s in steps
            ],
            "status": build_status(),
        }
        with STATE_LOCK:
            STATE["last_refresh"] = result
            if not ok:
                STATE["last_error"] = "one_or_more_steps_failed"
        return result
    except Exception as exc:  # noqa: BLE001
        err = f"{exc}\n{traceback.format_exc()}"
        with STATE_LOCK:
            STATE["last_error"] = str(exc)
        return {"ok": False, "mode": mode, "error": str(exc), "detail": err[-2000:]}
    finally:
        with STATE_LOCK:
            STATE["refresh_running"] = False


def build_status() -> dict[str, Any]:
    ensure_dirs()
    latest_site = _read_json(SITE / "latest.json")
    latest_docs = _read_json(DOCS / "data" / "latest.json")
    latest = latest_docs or latest_site
    build_info = _read_json(DOCS / "data" / "build_info.json") or _read_json(SITE / "build_info.json")

    update_time = latest.get("update_time")
    age_minutes = None
    if update_time:
        try:
            ts = datetime.fromisoformat(str(update_time).replace("Z", "+00:00"))
            age_minutes = max(0, int((datetime.now(ts.tzinfo or timezone.utc) - ts).total_seconds() // 60))
        except Exception:
            age_minutes = None

    stale = age_minutes is None or age_minutes > 12 * 60  # > 12h treated stale for daily market data
    with STATE_LOCK:
        refresh_running = STATE["refresh_running"]
        last_refresh = STATE["last_refresh"]
        last_error = STATE["last_error"]
        started_at = STATE["started_at"]

    return {
        "service": "a-share-risk-thermometer-app-server",
        "data_plane": "local_pipeline",
        "independent_of_github": True,
        "github_pages_used": False,
        "root": str(ROOT),
        "started_at": started_at,
        "server_time": _now_iso(),
        "refresh_running": refresh_running,
        "last_error": last_error,
        "last_refresh": {
            "ok": (last_refresh or {}).get("ok"),
            "mode": (last_refresh or {}).get("mode"),
            "finished_at": (last_refresh or {}).get("finished_at"),
        }
        if last_refresh
        else None,
        "latest": {
            "trade_date": latest.get("trade_date"),
            "update_time": latest.get("update_time"),
            "risk_temperature": latest.get("risk_temperature"),
            "regime_cn": latest.get("regime_cn"),
            "temperature_mode": latest.get("temperature_mode"),
            "temperature_mode_cn": latest.get("temperature_mode_cn"),
            "is_final": latest.get("is_final"),
            "model_confidence_label": latest.get("model_confidence_label"),
            "headline": (latest.get("interpretation") or {}).get("headline"),
        },
        "build_info": build_info,
        "freshness": {
            "age_minutes": age_minutes,
            "stale": stale,
            "policy": "prefer_local_pipeline; refresh via POST /api/refresh",
        },
        "paths": {
            "site": str(SITE),
            "docs_data": str(DOCS / "data"),
            "calculated": str(CALCULATED),
        },
    }


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, fmt: str, *args) -> None:  # quieter
        sys.stderr.write("[app_server] " + (fmt % args) + "\n")

    def _send_json(self, payload: dict[str, Any], code: int = 200) -> None:
        body = dumps_json(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json({"ok": True, "time": _now_iso()})
            return
        if parsed.path == "/api/status":
            self._send_json(build_status())
            return
        # bust-cache friendly static
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/refresh":
            self._send_json({"ok": False, "error": "not_found"}, 404)
            return
        qs = parse_qs(parsed.query)
        mode = (qs.get("mode") or ["realtime"])[0]
        # Allow JSON body mode override
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                mode = body.get("mode") or mode
            except Exception:
                pass

        # Long-running: run in thread, return immediately with 202 for full;
        # realtime/rebuild usually wait (simpler for UI).
        if mode == "full":
            def _job() -> None:
                refresh_pipeline("full")

            with STATE_LOCK:
                if STATE["refresh_running"]:
                    self._send_json({"ok": False, "error": "refresh_already_running"}, 409)
                    return
            t = threading.Thread(target=_job, daemon=True)
            t.start()
            self._send_json(
                {
                    "ok": True,
                    "accepted": True,
                    "mode": "full",
                    "message": "full refresh started in background; poll /api/status",
                    "status": build_status(),
                },
                202,
            )
            return

        result = refresh_pipeline(mode)
        self._send_json(result, 200 if result.get("ok") else 500)


def bootstrap_fast() -> dict[str, Any]:
    """Bring UI + cached site JSON online quickly (no network)."""
    ensure_dirs()
    _sync_web_to_docs()
    print("[app_server] rebuild site data from local pipeline cache…")
    rebuild = refresh_pipeline("rebuild")
    print("[app_server] rebuild ok=", rebuild.get("ok"), rebuild.get("steps"))
    _sync_site_data_to_docs()
    return rebuild


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent app data server (no GitHub dependency)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("APP_PORT", "8787")))
    parser.add_argument(
        "--auto-refresh",
        choices=["none", "rebuild", "realtime", "full"],
        default=os.environ.get("APP_AUTO_REFRESH", "realtime"),
        help="After listen: none|rebuild|realtime|full (default realtime, background)",
    )
    args = parser.parse_args()

    STATE["started_at"] = _now_iso()
    # Always get cached site online first so Expo can connect immediately.
    bootstrap_fast()

    handler = partial(AppHandler, directory=str(DOCS))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    status = build_status()
    print(
        f"[app_server] listening on http://{args.host}:{args.port}\n"
        f"  data_plane=local_pipeline independent_of_github=true\n"
        f"  RT={status['latest'].get('risk_temperature')} "
        f"mode={status['latest'].get('temperature_mode')} "
        f"asof={status['latest'].get('update_time')}\n"
        f"  refresh: POST /api/refresh?mode=realtime|full|rebuild\n"
        f"  status:  GET  /api/status"
    )

    auto = args.auto_refresh
    if auto in {"realtime", "full"}:
        def _bg() -> None:
            print(f"[app_server] background auto_refresh mode={auto}…")
            result = refresh_pipeline(auto)
            print("[app_server] background auto_refresh ok=", result.get("ok"), result.get("steps"))

        threading.Thread(target=_bg, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[app_server] stopped")


if __name__ == "__main__":
    main()
