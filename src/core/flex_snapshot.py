from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from src.storage.json_store import dumps_json, read_json, write_json
from src.storage.paths import DOCS, SITE
from src.utils.dates import now_cn


FLEX_SNAPSHOT_SCHEMA_VERSION = 1
FLEX_SNAPSHOT_NAME = "flex_snapshot.json"


def build_flex_snapshot(
    stage_playbook: dict[str, Any],
    etf_daily_marks: dict[str, Any],
    trade_calendar: dict[str, Any],
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
    }
    digest = hashlib.sha256(
        dumps_json(content, indent=None).encode("utf-8")
    ).hexdigest()
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
        "calendar_coverage_through": trade_calendar.get("coverage_through"),
        **content,
    }


def publish_flex_snapshot(
    *,
    site_dir: Path = SITE,
    docs_dir: Path = DOCS,
) -> dict[str, Any]:
    snapshot = build_flex_snapshot(
        read_json(site_dir / "stage_playbook.json", default={}) or {},
        read_json(site_dir / "etf_daily_marks.json", default={}) or {},
        read_json(site_dir / "trade_calendar.json", default={}) or {},
    )
    write_json(snapshot, site_dir / FLEX_SNAPSHOT_NAME)
    docs_data = docs_dir / "data"
    if docs_dir.exists():
        write_json(snapshot, docs_data / FLEX_SNAPSHOT_NAME)
    return snapshot
