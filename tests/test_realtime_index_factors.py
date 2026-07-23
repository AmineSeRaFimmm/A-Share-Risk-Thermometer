from __future__ import annotations

import pandas as pd

from src.core.realtime_index_factors import augment_index_history_with_realtime
from src.data_sources.eastmoney_indices import _parse_index_payload
from src.data_sources.eastmoney_indices import SOURCE_TENCENT_INDEX_RT, fetch_realtime_index_snapshot


def test_parse_eastmoney_index_quote_scales_price_and_volume():
    row = _parse_index_payload(
        {"data": {"f43": 471810, "f44": 473751, "f45": 469887, "f46": 473517, "f47": 198989092, "f48": 613815985884.5, "f60": 471724}},
        "sh000300",
        "2026-07-23",
    )
    assert row["close"] == 4718.1
    assert row["previous_close"] == 4717.24
    assert row["volume"] == 19898909200.0


def test_realtime_index_row_replaces_same_day_without_mutating_history():
    history = pd.DataFrame(
        [
            {"date": "2026-07-22", "symbol": "sh000300", "close": 4717.24, "volume": 2.8e10, "source": "EOD"},
            {"date": "2026-07-22", "symbol": "sh000001", "close": 3867.03, "volume": 3.0e10, "source": "EOD"},
        ]
    )
    snapshot = pd.DataFrame(
        [{"trade_date": "2026-07-23", "symbol": "sh000300", "close": 4718.1, "volume": 1.9e10, "source": "RT", "quote_time": "2026-07-23T13:00:00+08:00"}]
    )
    out = augment_index_history_with_realtime(history, snapshot, "2026-07-23")
    assert len(history) == 2
    assert len(out) == 3
    assert float(out.iloc[-1]["close"]) == 4718.1
    assert out.iloc[-1]["source"] == "RT"


def test_realtime_index_uses_tencent_when_eastmoney_fails(monkeypatch):
    class Response:
        text = 'v_s_sh000300="1~HS300~000300~4718.16~0.92~0.02~201864460~62007771~~557320.12~ZS~";\nv_s_sh000001="1~SSE~000001~3867.95~0.92~0.02~456828015~85516701~~644747.09~ZS~";'

        def raise_for_status(self):
            return None

        def json(self):
            raise RuntimeError("eastmoney unavailable")

    def fake_get(url, **_kwargs):
        if "push2.eastmoney.com" in url:
            raise RuntimeError("eastmoney unavailable")
        return Response()

    monkeypatch.setattr("src.data_sources.eastmoney_indices.requests.get", fake_get)
    out = fetch_realtime_index_snapshot("2026-07-23")
    assert len(out) == 2
    assert set(out["source"]) == {SOURCE_TENCENT_INDEX_RT}
    assert float(out.loc[out["symbol"] == "sh000300", "close"].iloc[0]) == 4718.16
