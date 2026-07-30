"""Tests for multi-source index daily merge/normalize."""
from __future__ import annotations

import pandas as pd

from src.data_sources.akshare_indices import (
    SOURCE_SINA_PARSE,
    SOURCE_TX_PARSE,
    _normalize_symbol,
    fetch_index_daily_tx_parse,
    merge_index_daily_sources,
)


def test_normalize_symbol():
    assert _normalize_symbol("sh000300") == "sh000300"
    assert _normalize_symbol("000300") == "sh000300"
    assert _normalize_symbol("399006") == "sz399006"


def test_merge_fills_missing_dates_only():
    primary = pd.DataFrame(
        [
            {
                "date": "2026-07-15",
                "open": 1.0,
                "close": 10.0,
                "high": 11.0,
                "low": 9.0,
                "volume": 100,
                "symbol": "sh000300",
                "source": SOURCE_SINA_PARSE,
                "fetch_time": "a",
            }
        ]
    )
    secondary = pd.DataFrame(
        [
            {
                "date": "2026-07-15",
                "open": 2.0,
                "close": 99.0,
                "high": 100.0,
                "low": 1.0,
                "volume": 1,
                "symbol": "sh000300",
                "source": SOURCE_TX_PARSE,
                "fetch_time": "b",
            },
            {
                "date": "2026-07-16",
                "open": 3.0,
                "close": 12.0,
                "high": 13.0,
                "low": 11.0,
                "volume": 200,
                "symbol": "sh000300",
                "source": SOURCE_TX_PARSE,
                "fetch_time": "b",
            },
        ]
    )
    out = merge_index_daily_sources([primary, secondary], "sh000300")
    by = out.set_index("date")
    # existing date keeps primary close
    assert float(by.loc["2026-07-15", "close"]) == 10.0
    assert by.loc["2026-07-15", "source"] == SOURCE_SINA_PARSE
    # missing date filled from secondary
    assert float(by.loc["2026-07-16", "close"]) == 12.0
    assert by.loc["2026-07-16", "source"] == SOURCE_TX_PARSE


def test_tencent_daily_volume_is_normalized_from_lots_to_shares(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "sh000300": {
                        "day": [["2026-07-30", "4566.0", "4549.72", "4596.2", "4478.2", "274651130"]]
                    }
                }
            }

    monkeypatch.setattr(
        "src.data_sources.akshare_indices.requests.Session.get",
        lambda *_args, **_kwargs: Response(),
    )
    row = fetch_index_daily_tx_parse("sh000300").iloc[0]
    assert float(row["volume"]) == 27_465_113_000.0
