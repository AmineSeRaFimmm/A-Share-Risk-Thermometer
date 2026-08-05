"""ETF EOD marks helpers for Flex sim book."""
from __future__ import annotations

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
