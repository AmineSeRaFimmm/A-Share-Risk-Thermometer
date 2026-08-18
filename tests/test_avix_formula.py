from __future__ import annotations

import pandas as pd

from src.core import avix_formula


def _chain(*dtes: int) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "trade_date": "2026-08-17",
            "expiry_date": f"2026-10-{index + 1:02d}",
            "dte": dte,
            "valid_price": True,
        }
        for index, dte in enumerate(dtes)
    ])


def _fake_term_variance(term: pd.DataFrame, _price_col: str, _rate: float) -> dict:
    dte = int(term["dte"].median())
    return {
        "dte": dte,
        "t": dte / 365,
        "variance": 0.04,
        "n_options": 24,
        "forward": 4700.0,
        "k0": 4700.0,
        "quality": "OK",
    }


def test_bounded_post_expiry_single_term_has_explicit_rollover_quality(monkeypatch):
    monkeypatch.setattr(avix_formula, "term_variance", _fake_term_variance)

    result = avix_formula.calculate_avix_for_date(
        _chain(32, 60), pd.DataFrame(), "2026-08-17"
    )

    assert result["near_dte"] == result["next_dte"] == 32
    assert "WARN_ROLLOVER_SINGLE_TERM_30D" in result["quality"]
    assert "WARN_NOT_BRACKET_30D" not in result["quality"]


def test_extended_holiday_rollover_is_identified_separately(monkeypatch):
    monkeypatch.setattr(avix_formula, "term_variance", _fake_term_variance)

    result = avix_formula.calculate_avix_for_date(
        _chain(43, 71), pd.DataFrame(), "2026-08-17"
    )

    assert "WARN_ROLLOVER_SINGLE_TERM_30D" in result["quality"]
    assert "WARN_ROLLOVER_EXTENDED_DTE" in result["quality"]


def test_distant_unbracketed_term_remains_unusable(monkeypatch):
    monkeypatch.setattr(avix_formula, "term_variance", _fake_term_variance)

    result = avix_formula.calculate_avix_for_date(
        _chain(47, 75), pd.DataFrame(), "2026-08-17"
    )

    assert "WARN_NOT_BRACKET_30D" in result["quality"]
    assert "WARN_ROLLOVER_SINGLE_TERM_30D" not in result["quality"]
