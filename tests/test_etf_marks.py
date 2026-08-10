"""ETF EOD marks helpers for Flex sim book."""
from __future__ import annotations

from threading import Lock
import time

from src.core.etf_marks import (
    FINAL_QUOTE_QUALITY,
    _parse_sina_final_quotes,
    _parse_tencent_final_quotes,
    bars_to_dict,
    build_etf_marks_payload,
    collect_flex_etf_codes,
    complete_payload_with_final_quotes,
)
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


def test_post_close_quote_parsers_require_same_day_final_timestamp() -> None:
    tencent = (
        'v_sh510300="1~name~510300~4.759~4.751~4.755~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~'
        '20260810153311~0~0~4.772~4.720";'
    )
    sina = (
        'var hq_str_sh515880="name,0.661,0.660,0.644,0.665,0.628,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,'
        '2026-08-10,15:33:33,00";'
    )
    tq = _parse_tencent_final_quotes(tencent, ["510300"], "2026-08-10")
    sq = _parse_sina_final_quotes(sina, ["515880"], "2026-08-10")
    assert tq["510300"]["open"] == 4.755
    assert tq["510300"]["close"] == 4.759
    assert sq["515880"]["open"] == 0.661
    assert sq["515880"]["close"] == 0.644
    assert _parse_tencent_final_quotes(tencent, ["510300"], "2026-08-07") == {}


def test_final_quotes_complete_lagging_daily_payload_with_provisional_quality() -> None:
    payload = {
        "as_of": "2026-08-10",
        "quality": "WARN_INCOMPLETE_AS_OF",
        "by_code": {
            "510300": {"etf_code": "510300", "bars": {"2026-08-07": {"open": 4.7, "close": 4.75, "high": 4.8, "low": 4.7}}},
            "515880": {"etf_code": "515880", "bars": {"2026-08-07": {"open": 0.65, "close": 0.66, "high": 0.67, "low": 0.64}}},
        },
    }
    quotes = {
        "510300": {"open": 4.755, "close": 4.759, "high": 4.772, "low": 4.72, "source": "TENCENT_ETF_FINAL_QUOTE", "quote_time": "2026-08-10T15:33:11+08:00"},
        "515880": {"open": 0.661, "close": 0.644, "high": 0.665, "low": 0.628, "source": "TENCENT_ETF_FINAL_QUOTE", "quote_time": "2026-08-10T15:33:19+08:00"},
    }
    completed = complete_payload_with_final_quotes(
        payload,
        codes=["510300", "515880"],
        target="2026-08-10",
        quotes=quotes,
    )
    assert completed["complete_as_of"] == "2026-08-10"
    assert completed["quality"] == FINAL_QUOTE_QUALITY
    assert completed["stale_codes"] == []
    assert completed["by_code"]["510300"]["bars"]["2026-08-10"]["open"] == 4.755
    assert completed["final_quote_fallback"]["code_count"] == 2
