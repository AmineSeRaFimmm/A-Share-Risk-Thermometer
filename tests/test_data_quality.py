from __future__ import annotations

from src.core.data_quality import observation_quality_score, quality_metadata


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
