from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.core.etf_marks import FINAL_QUOTE_QUALITY
from scripts import update_etf_eod_marks as updater
from scripts.update_etf_eod_marks import payload_is_complete, resolve_target_trade_date


ROOT = Path(__file__).resolve().parents[1]


def test_authoritative_calendar_skips_weekday_holiday() -> None:
    calendar = {
        "authoritative": True,
        "coverage_from": "2026-01-01",
        "coverage_through": "2026-12-31",
        "dates": ["2026-02-13", "2026-02-24"],
    }
    assert resolve_target_trade_date(calendar, current=date(2026, 2, 17)) is None
    assert resolve_target_trade_date(calendar, current=date(2026, 2, 24)) == "2026-02-24"


def test_explicit_recovery_date_and_complete_gate() -> None:
    assert resolve_target_trade_date({}, explicit="2026-08-07") == "2026-08-07"
    with pytest.raises(ValueError, match="invalid ETF EOD trade date"):
        resolve_target_trade_date({}, explicit="2026-08")
    complete = {
        "quality": "OK",
        "complete_as_of": "2026-08-07",
        "missing_codes": [],
        "stale_codes": [],
    }
    assert payload_is_complete(complete, "2026-08-07") is True
    assert payload_is_complete({**complete, "quality": FINAL_QUOTE_QUALITY}, "2026-08-07") is True
    assert payload_is_complete({**complete, "stale_codes": ["510300"]}, "2026-08-07") is False
    assert payload_is_complete({**complete, "complete_as_of": "2026-08-06"}, "2026-08-07") is False


def test_workflow_starts_at_1530_and_retries() -> None:
    workflow = (ROOT / ".github/workflows/update-etf-eod-marks.yml").read_text(encoding="utf-8")
    assert 'cron: "30,45 7 * * 1-5"' in workflow
    assert "a later tick will retry" in workflow
    assert "data/site/etf_daily_marks.json docs/data/etf_daily_marks.json docs/data/build_info.json" in workflow


def test_recovery_fetch_is_serial_to_avoid_embedded_v8_crashes() -> None:
    script = (ROOT / "scripts/update_etf_eod_marks.py").read_text(encoding="utf-8")
    assert "max_workers=1" in script


def test_daily_confirmation_changes_frontend_revision(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(updater, "DOCS", tmp_path)
    updater._publish_build_revision("2026-08-10", FINAL_QUOTE_QUALITY)
    provisional = updater._read_json(tmp_path / "data" / "build_info.json")
    updater._publish_build_revision("2026-08-10", "OK")
    confirmed = updater._read_json(tmp_path / "data" / "build_info.json")
    assert provisional["etf_marks_revision"].endswith(FINAL_QUOTE_QUALITY)
    assert confirmed["etf_marks_revision"] == "2026-08-10|OK"
    assert confirmed["build_time"] >= provisional["build_time"]
