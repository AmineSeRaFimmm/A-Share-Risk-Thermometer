from __future__ import annotations

import pandas as pd

from scripts.bootstrap_history import _trim_unusable_official_avix_tip
from scripts.update_daily import _official_avix_is_current
from src.core.official_avix_quality import assess_official_avix, official_avix_ready


def _row(**overrides):
    row = {
        "trade_date": "2026-08-10",
        "avix_clean": 19.47,
        "quality": "OK",
        "near_expiry": "2026-08-21",
        "next_expiry": "2026-09-18",
        "near_dte": 11,
        "next_dte": 39,
        "near_n_options": 26,
        "next_n_options": 28,
    }
    row.update(overrides)
    return row


def test_complete_two_term_avix_is_official_ready():
    assert official_avix_ready(_row())


def test_exact_30_day_term_is_official_ready():
    assert official_avix_ready(
        _row(
            near_expiry="2026-09-09",
            next_expiry="2026-09-09",
            near_dte=30,
            next_dte=30,
        )
    )


def test_single_term_fault_is_not_official_ready():
    assessment = assess_official_avix(
        _row(
            avix_clean=18.9934,
            quality="WARN_NOT_BRACKET_30D",
            near_expiry="2026-09-18",
            next_expiry="2026-09-18",
            near_dte=39,
            next_dte=39,
            near_n_options=14,
            next_n_options=14,
        )
    )
    assert not assessment.ready
    assert "NOT_BRACKET_30D" in assessment.reasons
    assert "TERM_STRUCTURE_NOT_30D" in assessment.reasons


def test_bounded_rollover_single_term_is_official_ready():
    assert official_avix_ready(
        _row(
            quality="WARN_ROLLOVER_SINGLE_TERM_30D",
            near_expiry="2026-09-18",
            next_expiry="2026-09-18",
            near_dte=32,
            next_dte=32,
        )
    )


def test_rollover_single_term_outside_bound_is_rejected():
    assessment = assess_official_avix(
        _row(
            quality="WARN_ROLLOVER_SINGLE_TERM_30D|WARN_ROLLOVER_EXTENDED_DTE",
            near_expiry="2026-09-18",
            next_expiry="2026-09-18",
            near_dte=47,
            next_dte=47,
        )
    )
    assert not assessment.ready
    assert "ROLLOVER_TERM_OUT_OF_RANGE" in assessment.reasons


def test_sparse_term_is_not_official_ready():
    assessment = assess_official_avix(_row(near_n_options=7))
    assert not assessment.ready
    assert "INSUFFICIENT_OPTIONS_PER_TERM" in assessment.reasons


def test_trim_removes_only_trailing_incomplete_tip():
    rows = pd.DataFrame(
        [
            _row(trade_date="2026-08-07", near_dte=14, next_dte=42),
            _row(
                avix_clean=18.9934,
                quality="WARN_NOT_BRACKET_30D",
                near_expiry="2026-09-18",
                next_expiry="2026-09-18",
                near_dte=39,
                next_dte=39,
                near_n_options=14,
                next_n_options=14,
            ),
        ]
    )
    trimmed = _trim_unusable_official_avix_tip(rows)
    assert trimmed["trade_date"].tolist() == ["2026-08-07"]


def test_trim_keeps_bounded_rollover_tip():
    rows = pd.DataFrame([
        _row(trade_date="2026-08-14", near_dte=7, next_dte=35),
        _row(
            trade_date="2026-08-17",
            quality="WARN_ROLLOVER_SINGLE_TERM_30D",
            near_expiry="2026-09-18",
            next_expiry="2026-09-18",
            near_dte=32,
            next_dte=32,
        ),
    ])
    trimmed = _trim_unusable_official_avix_tip(rows)
    assert trimmed["trade_date"].tolist() == ["2026-08-14", "2026-08-17"]


def test_daily_update_does_not_early_exit_on_incomplete_tip():
    partial = pd.DataFrame(
        [
            _row(
                avix_clean=18.9934,
                quality="WARN_NOT_BRACKET_30D",
                near_expiry="2026-09-18",
                next_expiry="2026-09-18",
                near_dte=39,
                next_dte=39,
                near_n_options=14,
                next_n_options=14,
            )
        ]
    )
    complete = pd.DataFrame([_row()])
    assert not _official_avix_is_current(partial, "2026-08-10")
    assert _official_avix_is_current(complete, "2026-08-10")
