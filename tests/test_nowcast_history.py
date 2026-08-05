from __future__ import annotations

from threading import Barrier

import pandas as pd

from src.core.nowcast_history import (
    _augment_breadth_for_realtime_dates,
    _augment_qvix_for_realtime_dates,
    _fetch_realtime_factor_inputs,
)


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


def test_augment_qvix_fetches_exact_positive_realtime_date():
    base = pd.DataFrame(
        [
            {
                "date": "2026-07-21",
                "open": 24.29,
                "high": 25.01,
                "low": 21.66,
                "close": 21.81,
                "source": "OPTBBS_PARSE_300ETF_QVIX",
            }
        ]
    )
    realtime = pd.DataFrame([{"trade_date": "2026-07-22"}])

    def fetcher(trade_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "date": trade_date,
                    "open": 21.74,
                    "high": 21.74,
                    "low": 21.28,
                    "close": 21.28,
                    "source": "OPTBBS_CSV_300ETF_MIN_QVIX_PROXY",
                }
            ]
        )

    out = _augment_qvix_for_realtime_dates(base, realtime, fetcher=fetcher)
    assert list(out["date"]) == ["2026-07-21", "2026-07-22"]
    row = out[out["date"] == "2026-07-22"].iloc[0]
    assert row["source"] == "OPTBBS_CSV_300ETF_MIN_QVIX_PROXY"
    assert float(row["close"]) == 21.28


def test_augment_qvix_rejects_mismatched_or_weak_rows():
    realtime = pd.DataFrame([{"trade_date": "2026-07-22"}])

    def mismatched(_trade_date: str) -> pd.DataFrame:
        return pd.DataFrame([{"date": "2026-07-21", "close": 21.28}])

    def weak(trade_date: str) -> pd.DataFrame:
        return pd.DataFrame([{"date": trade_date, "close": None}])

    assert _augment_qvix_for_realtime_dates(pd.DataFrame(), realtime, fetcher=mismatched).empty
    assert _augment_qvix_for_realtime_dates(pd.DataFrame(), realtime, fetcher=weak).empty


def test_realtime_factor_fetches_run_concurrently(monkeypatch):
    from src.core import nowcast_history as mod

    barrier = Barrier(3, timeout=1.0)

    def qvix(*_args):
        barrier.wait()
        return pd.DataFrame([{"date": "2026-07-22", "close": 20.0}])

    def index(*_args):
        barrier.wait()
        return pd.DataFrame([{"symbol": "sh000300", "close": 4000.0}])

    def breadth(*_args):
        barrier.wait()
        return pd.DataFrame([{"trade_date": "2026-07-22", "quality": "OK"}])

    monkeypatch.setattr(mod, "_augment_qvix_for_realtime_dates", qvix)
    monkeypatch.setattr(mod, "fetch_realtime_index_snapshot", index)
    monkeypatch.setattr(mod, "_augment_breadth_for_realtime_dates", breadth)
    q, i, b = _fetch_realtime_factor_inputs(
        pd.DataFrame(),
        pd.DataFrame([{"trade_date": "2026-07-22"}]),
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
    )
    assert float(q.iloc[0]["close"]) == 20.0
    assert float(i.iloc[0]["close"]) == 4000.0
    assert b.iloc[0]["quality"] == "OK"
