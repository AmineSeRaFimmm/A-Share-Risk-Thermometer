from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.backtest_flex_v2 import _path_total, instrument_tail_close_path_returns
from src.core.core_tail_policy import CORE_TAIL_EXECUTION_MODE, core_tail_condition_status
from src.core.flex_engine import CORE_HOLD_DAYS, simulate_positions


def _risk_panel(*, rt: float, dd60: float, confidence: float) -> pd.DataFrame:
    dates = pd.bdate_range("2026-07-20", periods=7)
    return pd.DataFrame(
        {
            "trade_date": dates.strftime("%Y-%m-%d"),
            "risk_temperature": [rt] * len(dates),
            "sh000300_dd60": [dd60] * len(dates),
            "model_confidence": [confidence] * len(dates),
        }
    )


def test_strict_core_uses_signal_day_tail_and_preserves_original_exit_date() -> None:
    panel = _risk_panel(rt=70.0, dd60=-0.07, confidence=96.0)
    state = simulate_positions(
        panel,
        None,
        active_stages_fn=lambda _feat: ["CSI300_CORE_BUY"],
        confirmed_core_tail_dates={"2026-07-20"},
    )

    assert state.core.entry_signal_date == "2026-07-20"
    assert state.core.entry_date == "2026-07-20"
    assert state.core.execution_mode == CORE_TAIL_EXECUTION_MODE
    assert state.core.entry_price_type == "tail_1450"
    assert state.core.planned_hold_days == CORE_HOLD_DAYS + 1
    assert state.core.days_held == CORE_HOLD_DAYS + 1


def test_non_strict_core_keeps_t_plus_one_open() -> None:
    panel = _risk_panel(rt=62.0, dd60=-0.055, confidence=96.0)
    state = simulate_positions(
        panel,
        None,
        active_stages_fn=lambda _feat: ["CSI300_CORE_BUY"],
    )

    assert state.core.entry_signal_date == "2026-07-20"
    assert state.core.entry_date == "2026-07-21"
    assert state.core.execution_mode == "T_PLUS_1_OPEN"
    assert state.core.planned_hold_days == CORE_HOLD_DAYS


def test_strict_daily_values_without_intraday_confirmation_keep_t_plus_one() -> None:
    panel = _risk_panel(rt=70.0, dd60=-0.07, confidence=96.0)
    state = simulate_positions(
        panel,
        None,
        active_stages_fn=lambda _feat: ["CSI300_CORE_BUY"],
    )

    assert state.core.entry_date == "2026-07-21"
    assert state.core.execution_mode == "T_PLUS_1_OPEN"


def test_tail_backtest_uses_close_to_close_path_without_return_smoothing() -> None:
    opens = np.array([100.0, 110.0, 120.0])
    closes = np.array([105.0, 115.0, 125.0])

    path = instrument_tail_close_path_returns(opens, closes, signal_i=0, exit_i=2)

    assert path is not None
    assert path[0] == 0.0
    assert path[1] == 115.0 / 105.0 - 1.0
    assert path[2] == 120.0 / 115.0 - 1.0
    assert _path_total(path) == pytest.approx(120.0 / 105.0 - 1.0)


def test_live_quality_gate_accepts_current_free_source_degradation() -> None:
    status = core_tail_condition_status(
        risk_temperature=65.3,
        hs300_drawdown_60d=-0.0817,
        model_confidence=86.8,
        model_coverage_score=100.0,
        model_data_quality_score=86.8,
        model_missing_components="QVIX_DELAYED",
        breadth_mode="STOCK_A",
        breadth_quality="OK",
        temperature_mode="NOWCAST",
    )

    assert status["state"] == "PASS"
    assert status["data_valid"] is True


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"model_coverage_score": 88.0}, "coverage"),
        ({"model_data_quality_score": 79.9}, "data_quality"),
        ({"model_missing_components": "STOCK_BREADTH"}, "allowed_degradations"),
        ({"breadth_mode": "INDEX_PROXY"}, "stock_breadth"),
        ({"breadth_quality": "WARN_BREADTH_PROXY"}, "stock_breadth"),
    ],
)
def test_live_quality_gate_marks_data_faults_invalid(overrides: dict, reason: str) -> None:
    values = {
        "risk_temperature": 70.0,
        "hs300_drawdown_60d": -0.07,
        "model_confidence": 86.8,
        "model_coverage_score": 100.0,
        "model_data_quality_score": 86.8,
        "model_missing_components": "QVIX_DELAYED",
        "breadth_mode": "STOCK_A",
        "breadth_quality": "OK",
        "temperature_mode": "NOWCAST",
    }
    values.update(overrides)

    status = core_tail_condition_status(**values)

    assert status["state"] == "INVALID"
    assert reason in status["invalid_reasons"]


def test_live_quality_gate_includes_exact_80_score_when_hard_gates_pass() -> None:
    status = core_tail_condition_status(
        risk_temperature=70.0,
        hs300_drawdown_60d=-0.07,
        model_confidence=80.0,
        model_coverage_score=100.0,
        model_data_quality_score=80.0,
        model_missing_components="QVIX_PROXY",
        breadth_mode="STOCK_A",
        breadth_quality="OK",
        temperature_mode="NOWCAST",
    )

    assert status["state"] == "PASS"
    assert status["checks"]["data_quality"] is True
