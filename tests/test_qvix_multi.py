"""Tests for multi-source QVIX parse/fallback."""
from __future__ import annotations

from io import StringIO

import pandas as pd

from src.data_sources.akshare_qvix import (
    SOURCE_AK_ETF,
    SOURCE_EOD_CROSS_CONFIRMED,
    SOURCE_RT_ETF_CSV,
    SOURCE_ETF,
    SOURCE_INDEX,
    _extract_pack,
    _normalize_min_qvix,
    build_cross_confirmed_eod_qvix,
    fetch_qvix,
    fetch_qvix_from_optbbs_parse,
    merge_qvix_cache,
)


def _eod_row(
    *,
    source: str,
    close: float,
    quote_time: str,
    sample_size: int,
    proxy: bool = False,
    delayed: bool = False,
    final: bool = True,
) -> pd.DataFrame:
    return pd.DataFrame([{
        "date": "2026-08-11",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "source": source,
        "qvix_quote_time": quote_time,
        "source_quote_time": quote_time,
        "sample_size": sample_size,
        "intraday_points": sample_size,
        "observed": True,
        "is_final": final,
        "is_proxy": proxy,
        "is_delayed": delayed,
        "quality_flags": "PROXY" if proxy else "DELAYED" if delayed else "OK",
    }])


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


def test_normalize_min_qvix_builds_intraday_proxy_bar():
    raw = pd.DataFrame(
        [
            {"time": "9:30:00", "qvix": "21.74"},
            {"time": "9:31:40", "qvix": "#NAME?"},
            {"time": "10:01:40", "qvix": "21.28"},
            {"time": "10:02:40", "qvix": ""},
        ]
    )
    out = _normalize_min_qvix(raw, "2026-07-22", SOURCE_RT_ETF_CSV)
    row = out.iloc[0]
    assert row["date"] == "2026-07-22"
    assert float(row["open"]) == 21.74
    assert float(row["close"]) == 21.28
    assert row["source"] == SOURCE_RT_ETF_CSV
    assert int(row["intraday_points"]) == 2
    assert row["qvix_quote_time"] == "2026-07-22T10:01:40+08:00"
    assert bool(row["is_proxy"])
    assert int(row["sample_size"]) == 2


def test_cross_confirmed_eod_qvix_accepts_two_strict_final_sources():
    etf = _eod_row(
        source=SOURCE_RT_ETF_CSV,
        close=19.48,
        quote_time="2026-08-11T15:00:38+08:00",
        sample_size=239,
        proxy=True,
    )
    eastmoney = _eod_row(
        source="EASTMONEY_CFFEX_300INDEX_QVIX_DELAYED",
        close=19.304886,
        quote_time="2026-08-11T15:31:19+08:00",
        sample_size=54,
        delayed=True,
    )

    row = build_cross_confirmed_eod_qvix("2026-08-11", etf, eastmoney).iloc[0]

    assert row["source"] == SOURCE_EOD_CROSS_CONFIRMED
    assert float(row["close"]) == 19.48
    assert float(row["secondary_close"]) == 19.304886
    assert row["secondary_source"] == "EASTMONEY_CFFEX_300INDEX_QVIX_DELAYED"
    assert float(row["source_agreement"]) > 0.99
    assert bool(row["is_proxy"])
    assert bool(row["is_delayed"])
    assert set(row["quality_flags"].split("|")) >= {
        "CROSS_CONFIRMED", "DELAYED", "EOD_PROVISIONAL", "PROXY",
    }


def test_cross_confirmed_eod_qvix_rejects_weak_or_divergent_inputs():
    valid_etf = _eod_row(
        source=SOURCE_RT_ETF_CSV,
        close=20.0,
        quote_time="2026-08-11T15:00:38+08:00",
        sample_size=239,
        proxy=True,
    )
    sparse_eastmoney = _eod_row(
        source="EASTMONEY_CFFEX_300INDEX_QVIX_DELAYED",
        close=20.1,
        quote_time="2026-08-11T15:31:19+08:00",
        sample_size=20,
        delayed=True,
    )
    divergent_eastmoney = _eod_row(
        source="EASTMONEY_CFFEX_300INDEX_QVIX_DELAYED",
        close=25.0,
        quote_time="2026-08-11T15:31:19+08:00",
        sample_size=54,
        delayed=True,
    )

    assert build_cross_confirmed_eod_qvix("2026-08-11", valid_etf, sparse_eastmoney).empty
    assert build_cross_confirmed_eod_qvix("2026-08-11", valid_etf, divergent_eastmoney).empty


def test_daily_backfill_replaces_provisional_but_not_the_reverse():
    provisional = _eod_row(
        source=SOURCE_EOD_CROSS_CONFIRMED,
        close=19.48,
        quote_time="2026-08-11T15:00:38+08:00",
        sample_size=239,
        proxy=True,
        delayed=True,
    )
    provisional["quality_flags"] = "CROSS_CONFIRMED|DELAYED|EOD_PROVISIONAL|PROXY"
    daily = pd.DataFrame([{
        "date": "2026-08-11",
        "open": 19.4,
        "high": 19.7,
        "low": 19.3,
        "close": 19.55,
        "source": SOURCE_ETF,
        "fetch_time": "2026-08-12T08:00:00+08:00",
    }])

    backfilled = merge_qvix_cache(daily, provisional).iloc[0]
    protected = merge_qvix_cache(provisional, daily).iloc[0]

    assert backfilled["source"] == SOURCE_ETF
    assert float(backfilled["close"]) == 19.55
    assert protected["source"] == SOURCE_ETF
    assert float(protected["close"]) == 19.55


def test_fetch_qvix_adds_cross_confirmed_eod_only_when_daily_is_missing(monkeypatch):
    monkeypatch.setattr(
        "src.data_sources.akshare_qvix.fetch_optbbs_k_csv",
        lambda **kwargs: _fake_k_csv(),
    )
    monkeypatch.setattr(
        "src.data_sources.akshare_qvix._fetch_akshare_qvix_merge",
        lambda: pd.DataFrame(),
    )
    provisional = _eod_row(
        source=SOURCE_EOD_CROSS_CONFIRMED,
        close=19.48,
        quote_time="2026-08-11T15:00:38+08:00",
        sample_size=239,
        proxy=True,
        delayed=True,
    )
    provisional["quality_flags"] = "CROSS_CONFIRMED|DELAYED|EOD_PROVISIONAL|PROXY"
    monkeypatch.setattr(
        "src.data_sources.akshare_qvix.fetch_cross_confirmed_eod_qvix",
        lambda trade_date, rate_curve, index_history: provisional,
    )

    out = fetch_qvix(
        eod_trade_date="2026-08-11",
        rate_curve=pd.DataFrame([{"date": "2026-08-11"}]),
        index_history=pd.DataFrame([{"date": "2026-08-11"}]),
    )

    row = out[out["date"] == "2026-08-11"].iloc[0]
    assert row["source"] == SOURCE_EOD_CROSS_CONFIRMED
    assert float(row["close"]) == 19.48


def test_fetch_qvix_does_not_fetch_eod_proxy_when_daily_exists(monkeypatch):
    raw = _fake_k_csv()
    extra = raw.iloc[[-1]].copy()
    extra.iloc[0, 0] = "2026-08-11"
    extra.iloc[0, 9:13] = [19.4, 19.7, 19.3, 19.55]
    raw = pd.concat([raw, extra], ignore_index=True)
    monkeypatch.setattr(
        "src.data_sources.akshare_qvix.fetch_optbbs_k_csv",
        lambda **kwargs: raw,
    )
    monkeypatch.setattr(
        "src.data_sources.akshare_qvix._fetch_akshare_qvix_merge",
        lambda: pd.DataFrame(),
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("EOD fallback must not run when a daily row exists")

    monkeypatch.setattr(
        "src.data_sources.akshare_qvix.fetch_cross_confirmed_eod_qvix",
        unexpected,
    )

    out = fetch_qvix(
        eod_trade_date="2026-08-11",
        rate_curve=pd.DataFrame([{"date": "2026-08-11"}]),
        index_history=pd.DataFrame([{"date": "2026-08-11"}]),
    )

    row = out[out["date"] == "2026-08-11"].iloc[0]
    assert row["source"] == SOURCE_ETF
    assert float(row["close"]) == 19.55
