from __future__ import annotations

import pandas as pd

from src.core.nowcast_history import _augment_breadth_for_realtime_dates


def test_augment_breadth_fetches_exact_ok_realtime_date():
    base = pd.DataFrame(
        [
            {
                "trade_date": "2026-07-21",
                "advancing_ratio": 0.56,
                "decline_ratio": 0.42,
                "big_down_ratio": 0.005,
                "limit_down_ratio": 0.005,
                "quality": "OK",
            }
        ]
    )
    realtime = pd.DataFrame([{"trade_date": "2026-07-22"}])

    def fetcher(trade_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "advancing_ratio": 0.40,
                    "decline_ratio": 0.56,
                    "big_down_ratio": 0.005,
                    "limit_down_ratio": 0.001,
                    "quality": "OK",
                    "source": "PARSE_EM_A_SPOT",
                }
            ]
        )

    out = _augment_breadth_for_realtime_dates(base, realtime, fetcher=fetcher)
    assert list(out["trade_date"]) == ["2026-07-21", "2026-07-22"]
    row = out[out["trade_date"] == "2026-07-22"].iloc[0]
    assert row["source"] == "PARSE_EM_A_SPOT"
    assert float(row["advancing_ratio"]) == 0.40


def test_augment_breadth_rejects_mismatched_or_weak_rows():
    realtime = pd.DataFrame([{"trade_date": "2026-07-22"}])

    def mismatched(_trade_date: str) -> pd.DataFrame:
        return pd.DataFrame([{"trade_date": "2026-07-21", "quality": "OK"}])

    def weak(trade_date: str) -> pd.DataFrame:
        return pd.DataFrame([{"trade_date": trade_date, "quality": "WARN_BREADTH_MISSING"}])

    assert _augment_breadth_for_realtime_dates(pd.DataFrame(), realtime, fetcher=mismatched).empty
    assert _augment_breadth_for_realtime_dates(pd.DataFrame(), realtime, fetcher=weak).empty
