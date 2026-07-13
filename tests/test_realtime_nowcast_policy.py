from __future__ import annotations

from src.core.realtime_avix import (
    realtime_avix_allows_gap_fill,
    realtime_avix_allows_nowcast,
)


def test_nowcast_allows_warn_not_bracket():
    assert realtime_avix_allows_nowcast("WARN_NOT_BRACKET_30D", 20.25) is True
    assert realtime_avix_allows_gap_fill("WARN_NOT_BRACKET_30D", 20.25) is False


def test_strict_ok_for_gap_fill():
    assert realtime_avix_allows_nowcast("OK", 20.25) is True
    assert realtime_avix_allows_gap_fill("OK", 20.25) is True


def test_reject_bad_and_low():
    assert realtime_avix_allows_nowcast("BAD_OUTLIER_VS_CLOSE_AVIX", 40.0) is False
    assert realtime_avix_allows_nowcast("LOW_TOO_FEW_MONTHS", 20.0) is False
    assert realtime_avix_allows_gap_fill("LOW_TOO_FEW_MONTHS", 20.0) is False


def test_reject_nonpositive_avix():
    assert realtime_avix_allows_nowcast("OK", 0) is False
    assert realtime_avix_allows_nowcast("WARN_NOT_BRACKET_30D", float("nan")) is False
