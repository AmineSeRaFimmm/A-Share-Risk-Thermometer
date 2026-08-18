from __future__ import annotations

import pandas as pd

from src.core.data_quality import observation_quality_score, quality_metadata
from src.core.risk_temperature import _model_confidence_details


def test_quality_metadata_distinguishes_quote_time_from_fetch_time():
    meta = quality_metadata(
        source="TEST",
        trade_date="2026-07-30",
        source_quote_time="14:56:40",
        fetch_time="2026-07-30T15:00:00+08:00",
        now="2026-07-30T15:00:00+08:00",
        sample_size=238,
        max_age_seconds=600,
    )

    assert meta["source_quote_time"] == "2026-07-30T14:56:40+08:00"
    assert meta["fetch_time"] == "2026-07-30T15:00:00+08:00"
    assert meta["age_seconds"] == 200
    assert meta["quality_flags"] == "OK"


def test_unverified_proxy_is_observed_but_quality_discounted():
    meta = quality_metadata(
        source="PROXY",
        trade_date="2026-07-30",
        source_quote_time=None,
        is_proxy=True,
        observed=True,
    )
    score = observation_quality_score(
        observed=meta["observed"],
        quality_flags=meta["quality_flags"],
    )

    assert meta["quality_flags"] == "PROXY|TIME_UNVERIFIED"
    assert score == 0.51


def test_missing_neutral_fill_never_counts_as_observed():
    assert observation_quality_score(observed=False, quality_flags="MISSING") == 0.0


def test_unbracketed_avix_terms_reduce_observation_quality():
    assert observation_quality_score(
        observed=True,
        quality_flags="WARN_NOT_BRACKET_30D",
    ) == 0.9


def test_rollover_avix_quality_reaches_model_confidence():
    details = _model_confidence_details(pd.Series({
        "avix_clean": 18.0,
        "avix_quality": "WARN_ROLLOVER_SINGLE_TERM_30D",
        "avix_is_final": True,
        "qvix_close": 18.1,
        "qvix_quality_flags": "OK",
        "is_final": True,
        "realized_vol_percentile": 50.0,
        "drawdown_pressure": 50.0,
        "breadth_pressure": 50.0,
        "breadth_quality_flags": "OK",
        "breadth_is_final": True,
        "turnover_stress": 50.0,
        "index_quality_flags": "OK",
        "index_is_final": True,
    }))

    assert details["coverage_score"] == 100.0
    assert details["data_quality_score"] == 95.0
    assert details["score"] == 95.0


def test_final_close_does_not_become_stale_after_market():
    meta = quality_metadata(
        source="EOD",
        trade_date="2026-07-30",
        source_quote_time="15:00:40",
        now="2026-07-30T17:00:00+08:00",
        is_final=True,
        max_age_seconds=15 * 60,
    )
    assert meta["is_final"] is True
    assert meta["age_seconds"] > 15 * 60
    assert meta["quality_flags"] == "OK"
    assert observation_quality_score(
        observed=True,
        quality_flags=meta["quality_flags"],
        age_seconds=meta["age_seconds"],
        max_age_seconds=15 * 60,
        is_final=True,
    ) == 1.0
