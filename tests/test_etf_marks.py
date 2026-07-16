"""ETF EOD marks helpers for Flex sim book."""
from __future__ import annotations

from src.core.etf_marks import bars_to_dict, collect_flex_etf_codes
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
