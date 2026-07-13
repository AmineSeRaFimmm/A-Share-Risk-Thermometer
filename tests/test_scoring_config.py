from __future__ import annotations

from src.utils.config import DEFAULT_WEIGHTS, load_regimes, load_thresholds, load_weights


def test_weights_sum_to_one():
    weights = load_weights()
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_weights_match_defaults():
    weights = load_weights()
    assert set(weights) == set(DEFAULT_WEIGHTS)
    for key, value in DEFAULT_WEIGHTS.items():
        assert abs(weights[key] - value) < 1e-12


def test_regimes_cover_full_range():
    regimes = load_regimes()
    assert regimes[0][0] == 20
    assert regimes[-1][1] == "EXTREME_PANIC"
    assert regimes[-1][0] >= 100


def test_thresholds_defaults():
    thr = load_thresholds()
    assert thr["min_qvix_corr_60"] == 0.60
    assert thr["min_history_days_for_percentile"] == 120
    assert thr["min_options_per_term"] == 8
    assert thr["fixed_warning_level"] == 22
    assert thr["fixed_panic_level"] == 25
