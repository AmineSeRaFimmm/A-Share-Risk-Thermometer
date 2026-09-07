from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from src.core.flex_daily_brief import build_daily_flex_brief
from src.core.flex_engine import (
    load_position_state, position_state_from_dict, refresh_published_flex_execution,
)
from src.storage.json_store import dumps_json, read_json, write_json
from src.storage.paths import DOCS, SITE
from src.utils.dates import now_cn


FLEX_SNAPSHOT_SCHEMA_VERSION = 2
FLEX_SNAPSHOT_NAME = "flex_snapshot.json"


def build_flex_snapshot(
    stage_playbook: dict[str, Any],
    etf_daily_marks: dict[str, Any],
    trade_calendar: dict[str, Any],
    intraday_temperature: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(stage_playbook.get("flex_panel"), dict):
        raise ValueError("stage_playbook.flex_panel is required")
    if not isinstance(etf_daily_marks.get("by_code"), dict):
        raise ValueError("etf_daily_marks.by_code is required")
    if not isinstance(trade_calendar.get("dates"), list):
        raise ValueError("trade_calendar.dates is required")

    content = {
        "stage_playbook": stage_playbook,
        "etf_daily_marks": etf_daily_marks,
        "trade_calendar": trade_calendar,
        "daily_strategy_brief": build_daily_flex_brief(
            stage_playbook,
            etf_daily_marks,
            trade_calendar,
            intraday_temperature,
        ),
    }
    digest = hashlib.sha256(
        dumps_json(content, indent=None).encode("utf-8")
    ).hexdigest()
    daily_brief = content["daily_strategy_brief"]
    brief_quality = daily_brief.get("data_quality") or {}
    return {
        "schema_version": FLEX_SNAPSHOT_SCHEMA_VERSION,
        "revision": digest,
        "built_at": now_cn().isoformat(timespec="seconds"),
        "strategy_as_of": str(stage_playbook.get("as_of") or "")[:10] or None,
        "marks_as_of": str(
            etf_daily_marks.get("complete_as_of") or etf_daily_marks.get("as_of") or ""
        )[:10]
        or None,
        "marks_quality": etf_daily_marks.get("quality"),
        "strategy_publication_status": brief_quality.get(
            "strategy_publication_status"
        ),
        "official_strategy_pending": bool(
            brief_quality.get("official_strategy_pending")
        ),
        "calendar_coverage_through": trade_calendar.get("coverage_through"),
        **content,
    }


def publish_flex_snapshot(
    *,
    site_dir: Path = SITE,
    docs_dir: Path = DOCS,
    position_state_path: Path | None = None,
) -> dict[str, Any]:
    from src.core.flex_engine import POSITION_STATE_PATH

    playbook = read_json(site_dir / "stage_playbook.json", default={}) or {}
    marks = read_json(site_dir / "etf_daily_marks.json", default={}) or {}
    calendar = read_json(site_dir / "trade_calendar.json", default={}) or {}
    state_path = position_state_path or POSITION_STATE_PATH
    saved = load_position_state(state_path)
    panel = playbook.get("flex_panel") or {}
    raw_state = panel.get("position_state")
    if isinstance(raw_state, dict) and saved.as_of and saved.as_of == raw_state.get("as_of"):
        # A previous publish may have saved fills before replacing the snapshot.
        # Never replace that durable history with an older same-day panel.
        saved_events = {e.get("event_id"): e for e in saved.execution_events}
        published_events = raw_state.get("execution_events") or []
        if all(e.get("event_id") in saved_events and not (
            e.get("execution_status") == "EXECUTED"
            and saved_events[e.get("event_id")].get("execution_status") != "EXECUTED"
        ) for e in published_events):
            panel["position_state"] = saved.to_dict()
    playbook = refresh_published_flex_execution(playbook, marks, calendar)
    snapshot = build_flex_snapshot(
        playbook,
        marks,
        calendar,
        read_json(site_dir / "intraday_temperature.json", default={}) or {},
    )
    raw_state = (playbook.get("flex_panel") or {}).get("position_state")
    if isinstance(raw_state, dict):
        state = position_state_from_dict(raw_state)
        if str(state.as_of or "") >= str(saved.as_of or ""):
            write_json(state.to_dict(), state_path)
    write_json(playbook, site_dir / "stage_playbook.json")
    write_json(snapshot, site_dir / FLEX_SNAPSHOT_NAME)
    docs_data = docs_dir / "data"
    if docs_dir.exists():
        write_json(playbook, docs_data / "stage_playbook.json")
        write_json(snapshot, docs_data / FLEX_SNAPSHOT_NAME)
    return snapshot
