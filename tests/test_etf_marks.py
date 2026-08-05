"""ETF EOD marks helpers for Flex sim book."""
from __future__ import annotations

from threading import Lock
import time

from src.core.etf_marks import bars_to_dict, build_etf_marks_payload, collect_flex_etf_codes
import pandas as pd


def test_collect_includes_csi300():
    codes = collect_flex_etf_codes(None)
    assert "510300" in codes


def test_bars_to_dict_rounds():
    df = pd.DataFrame(
        [
            {"trade_date": "2026-07-14", "open": 4.8361, "close": 4.7982, "high": 4.9, "low": 4.7},
            {"trade_date": "2026-07-15", "open": 4.829, "close": 4.838, "high": 4.87, "low": 4.81},
        ]
    )
    d = bars_to_dict(df)
    assert d["2026-07-14"]["open"] == 4.8361 or abs(d["2026-07-14"]["open"] - 4.8361) < 1e-6
    assert "close" in d["2026-07-15"]


def test_payload_reports_stale_codes_and_common_complete_date(monkeypatch):
    def fake_load(code, **_kwargs):
        last = "2026-07-15" if code == "510300" else "2026-07-14"
        return pd.DataFrame([
            {"trade_date": "2026-07-14", "open": 1, "close": 1, "high": 1, "low": 1},
            *([{"trade_date": last, "open": 1, "close": 1, "high": 1, "low": 1}]
              if last != "2026-07-14" else []),
        ])

    monkeypatch.setattr("src.core.etf_marks.collect_flex_etf_codes", lambda _playbook: ["510300", "515880"])
    monkeypatch.setattr("src.core.etf_marks.load_or_fetch_etf_bars", fake_load)
    payload = build_etf_marks_payload(as_of="2026-07-15", playbook={})
    assert payload["stale_codes"] == ["515880"]
    assert payload["complete_as_of"] == "2026-07-14"
    assert payload["quality"] == "WARN_INCOMPLETE_AS_OF"
    assert payload["by_code"]["510300"]["fresh_for_as_of"] is True


def test_etf_marks_fetch_codes_with_bounded_parallelism(monkeypatch):
    from src.core import etf_marks as mod

    codes = ["510300", "510310", "510500", "512480"]
    active = 0
    max_active = 0
    lock = Lock()

    def fetch(code, **_kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return pd.DataFrame([{
            "trade_date": "2026-07-15",
            "open": 1.0,
            "close": 1.1,
            "high": 1.2,
            "low": 0.9,
        }])

    monkeypatch.setattr(mod, "collect_flex_etf_codes", lambda _playbook: codes)
    monkeypatch.setattr(mod, "load_or_fetch_etf_bars", fetch)
    payload = build_etf_marks_payload(as_of="2026-07-15", max_workers=2)
    assert max_active == 2
    assert list(payload["by_code"]) == codes
    assert payload["fetch_workers"] == 2
