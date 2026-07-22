"""Tests for multi-source QVIX parse/fallback."""
from __future__ import annotations

from io import StringIO

import pandas as pd

from src.data_sources.akshare_qvix import (
    SOURCE_AK_ETF,
    SOURCE_ETF,
    SOURCE_INDEX,
    _extract_pack,
    fetch_qvix,
    fetch_qvix_from_optbbs_parse,
    merge_qvix_cache,
)


def _fake_k_csv() -> pd.DataFrame:
    # Build a wide frame with date + packs at the same iloc positions as optbbs k.csv
    n_cols = 21
    rows = []
    # index good historically, then broken; etf always good
    for i, (day, idx_c, etf_c) in enumerate(
        [
            ("2026-07-08", 20.0, 19.5),
            ("2026-07-09", 21.0, 20.0),
            ("2026-07-10", None, 21.09),  # index missing, etf ok
            ("2026-07-16", None, 22.69),
        ]
    ):
        row = [None] * n_cols
        row[0] = day
        # 300etf pack 9..12
        row[9], row[10], row[11], row[12] = etf_c, etf_c, etf_c, etf_c
        # 300index pack 17..20
        if idx_c is None:
            row[17] = row[18] = row[19] = row[20] = "#NAME?"
        else:
            row[17] = row[18] = row[19] = row[20] = idx_c
        rows.append(row)
    return pd.DataFrame(rows)


def test_extract_pack_coerces_name_errors():
    raw = _fake_k_csv()
    idx = _extract_pack(raw, "300index", SOURCE_INDEX)
    etf = _extract_pack(raw, "300etf", SOURCE_ETF)
    assert list(idx["date"]) == ["2026-07-08", "2026-07-09"]
    assert list(etf["date"]) == ["2026-07-08", "2026-07-09", "2026-07-10", "2026-07-16"]


def test_merge_prefers_index_then_etf(monkeypatch):
    raw = _fake_k_csv()
    monkeypatch.setattr(
        "src.data_sources.akshare_qvix.fetch_optbbs_k_csv",
        lambda **kwargs: raw,
    )
    out, meta = fetch_qvix_from_optbbs_parse()
    assert meta["etf_used_as_fallback"] == 2
    by = out.set_index("date")["close"].to_dict()
    assert by["2026-07-09"] == 21.0
    assert by["2026-07-10"] == 21.09
    assert by["2026-07-16"] == 22.69
    src = out.set_index("date")["source"].to_dict()
    assert src["2026-07-09"] == SOURCE_INDEX
    assert src["2026-07-16"] == SOURCE_ETF


def test_merge_cache_does_not_overwrite_with_nan():
    cached = pd.DataFrame(
        [{"date": "2026-07-16", "open": 1, "high": 1, "low": 1, "close": 22.0, "source": "OLD", "fetch_time": "a"}]
    )
    fresh = pd.DataFrame(
        [{"date": "2026-07-16", "open": None, "high": None, "low": None, "close": None, "source": "NEW", "fetch_time": "b"}]
    )
    m = merge_qvix_cache(fresh, cached)
    assert float(m.iloc[0]["close"]) == 22.0
    assert m.iloc[0]["source"] == "OLD"


def test_fetch_qvix_fills_stale_optbbs_tail_from_akshare_etf(monkeypatch):
    raw = _fake_k_csv()
    monkeypatch.setattr(
        "src.data_sources.akshare_qvix.fetch_optbbs_k_csv",
        lambda **kwargs: raw,
    )

    def fake_akshare(fn_name: str, source: str) -> pd.DataFrame:
        if fn_name == "index_option_300etf_qvix":
            return pd.DataFrame(
                [
                    {
                        "date": "2026-07-21",
                        "open": 24.29,
                        "high": 25.01,
                        "low": 21.66,
                        "close": 21.81,
                        "source": source,
                        "fetch_time": "test",
                    }
                ]
            )
        return pd.DataFrame()

    monkeypatch.setattr("src.data_sources.akshare_qvix._fetch_akshare_series", fake_akshare)

    out = fetch_qvix()
    by = out.set_index("date")
    assert float(by.loc["2026-07-16", "close"]) == 22.69
    assert float(by.loc["2026-07-21", "close"]) == 21.81
    assert by.loc["2026-07-21", "source"] == SOURCE_AK_ETF
