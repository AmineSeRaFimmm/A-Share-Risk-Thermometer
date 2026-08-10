from __future__ import annotations

from datetime import date

import pandas as pd

from src.core import calendar


def test_trade_calendar_payload_marks_provider_coverage_authoritative(monkeypatch) -> None:
    index = pd.DataFrame({"date": ["2026-02-13", "2026-02-24"]})
    provider_days = [date(2026, 2, 13), date(2026, 2, 24), date(2026, 2, 25)]
    monkeypatch.setattr(calendar, "trading_days_from_akshare", lambda: provider_days)

    payload = calendar.trading_calendar_payload(index, current=date(2026, 2, 17))

    assert payload["authoritative"] is True
    assert payload["source"] == "AKSHARE_SINA_TRADE_DATE"
    assert payload["coverage_through"] == "2026-02-25"
    assert "2026-02-17" not in payload["dates"]
    assert payload["dates"] == ["2026-02-13", "2026-02-24", "2026-02-25"]


def test_trade_calendar_payload_labels_index_only_fallback(monkeypatch) -> None:
    index = pd.DataFrame({"date": ["2026-08-07", "2026-08-10"]})
    monkeypatch.setattr(calendar, "trading_days_from_akshare", lambda: [])

    payload = calendar.trading_calendar_payload(index, current=date(2026, 8, 10))

    assert payload["authoritative"] is False
    assert payload["source"] == "INDEX_HISTORY_ONLY"
    assert payload["coverage_through"] == "2026-08-10"
