#!/usr/bin/env python3
"""Refresh Flex ETF official closes without rebuilding the full daily pipeline."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.etf_marks import (
    FINAL_QUOTE_QUALITY,
    build_etf_marks_payload,
    collect_flex_etf_codes,
    complete_payload_with_final_quotes,
    fetch_etf_final_quotes,
    write_etf_marks_site,
)
from src.core.flex_snapshot import publish_flex_snapshot
from src.storage.json_store import write_json
from src.storage.paths import DOCS, SITE
from src.utils.dates import now_cn, today_cn


INCOMPLETE_EXIT = 2


def resolve_target_trade_date(
    calendar_payload: dict[str, Any] | None,
    *,
    current: date | None = None,
    explicit: str | None = None,
) -> str | None:
    if explicit:
        target = str(explicit).strip()
        try:
            return date.fromisoformat(target).isoformat()
        except ValueError as exc:
            raise ValueError(f"invalid ETF EOD trade date: {target}") from exc

    current = current or today_cn()
    target = current.isoformat()
    calendar = calendar_payload or {}
    coverage_from = str(calendar.get("coverage_from") or "")[:10]
    coverage_through = str(calendar.get("coverage_through") or "")[:10]
    if (
        calendar.get("authoritative")
        and coverage_from
        and coverage_through
        and coverage_from <= target <= coverage_through
    ):
        return target if target in set(calendar.get("dates") or []) else None

    # Calendar publication is optional. Weekday fallback keeps manual recovery
    # available, while scheduled weekend runs remain no-ops.
    return target if current.weekday() < 5 else None


def payload_is_complete(payload: dict[str, Any], target: str) -> bool:
    return bool(
        payload.get("quality") in {"OK", FINAL_QUOTE_QUALITY}
        and payload.get("complete_as_of") == target
        and not payload.get("missing_codes")
        and not payload.get("stale_codes")
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _publish_build_revision(target: str, quality: str, flex_revision: str) -> None:
    path = DOCS / "data" / "build_info.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _read_json(path)
    revision = f"{target}|{quality}"
    if (
        current.get("etf_marks_revision") == revision
        and current.get("flex_snapshot_revision") == flex_revision
    ):
        return
    write_json(
        {
            **current,
            "build_time": now_cn().isoformat(timespec="seconds"),
            "etf_marks_as_of": target,
            "etf_marks_quality": quality,
            "etf_marks_revision": revision,
            "flex_snapshot_revision": flex_revision,
            "revision_reason": "ETF_EOD_MARKS",
        },
        path,
    )


def refresh_etf_eod_marks(*, trade_date: str | None = None) -> dict[str, Any] | None:
    calendar = _read_json(SITE / "trade_calendar.json")
    target = resolve_target_trade_date(calendar, explicit=trade_date)
    if target is None:
        print("ETF EOD refresh skipped: today is not an A-share trading day.")
        return None

    published = _read_json(SITE / "etf_daily_marks.json")
    published_through = str(published.get("complete_as_of") or "")[:10]
    if published.get("quality") == "OK" and payload_is_complete(published, published_through) and published_through >= target:
        snapshot = publish_flex_snapshot()
        _publish_build_revision(
            published_through,
            str(published.get("quality") or "OK"),
            snapshot["revision"],
        )
        print(f"ETF EOD marks already complete through {published_through}.")
        return published

    playbook = _read_json(SITE / "stage_playbook.json")
    payload = build_etf_marks_payload(
        as_of=target,
        playbook=playbook,
        force_fetch=True,
        # AkShare's Eastmoney decoder uses an embedded V8 runtime which is not
        # thread-safe on every runner. EOD recovery favors deterministic fetches.
        max_workers=1,
    )
    if not payload_is_complete(payload, target):
        codes = collect_flex_etf_codes(playbook)
        quotes = fetch_etf_final_quotes(codes, target)
        payload = complete_payload_with_final_quotes(
            payload,
            codes=codes,
            target=target,
            quotes=quotes,
        )
    if not payload_is_complete(payload, target):
        print(
            "ETF EOD source is not complete yet: "
            f"target={target} complete_as_of={payload.get('complete_as_of')} "
            f"missing={payload.get('missing_codes') or []} "
            f"stale={payload.get('stale_codes') or []}",
            file=sys.stderr,
        )
        raise SystemExit(INCOMPLETE_EXIT)

    if (
        published.get("quality") == FINAL_QUOTE_QUALITY
        and payload.get("quality") == FINAL_QUOTE_QUALITY
        and published.get("complete_as_of") == target
        and all(
            (published.get("by_code", {}).get(code, {}).get("bars", {}).get(target)
             == payload.get("by_code", {}).get(code, {}).get("bars", {}).get(target))
            for code in collect_flex_etf_codes(playbook)
        )
    ):
        snapshot = publish_flex_snapshot()
        _publish_build_revision(
            target,
            str(payload.get("quality") or FINAL_QUOTE_QUALITY),
            snapshot["revision"],
        )
        print(f"ETF post-close marks already published for {target}; daily confirmation still pending.")
        return published

    write_etf_marks_site(payload)
    docs_data = DOCS / "data"
    docs_data.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SITE / "etf_daily_marks.json", docs_data / "etf_daily_marks.json")
    snapshot = publish_flex_snapshot()
    _publish_build_revision(
        target,
        str(payload.get("quality") or "OK"),
        snapshot["revision"],
    )
    print(
        "ETF EOD refresh complete: "
        f"target={target} codes={payload.get('code_count')} "
        f"complete_as_of={payload.get('complete_as_of')}"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade-date", help="explicit YYYY-MM-DD recovery target")
    args = parser.parse_args()
    refresh_etf_eod_marks(trade_date=args.trade_date)


if __name__ == "__main__":
    main()
