from __future__ import annotations

import pandas as pd

from src.core.flex_engine import FlexState, SleevePos, advance_positions


def _risk_through(last: str) -> pd.DataFrame:
    dates = [
        "2026-07-29",
        "2026-07-30",
        "2026-07-31",
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
        "2026-08-07",
        "2026-08-10",
        "2026-08-11",
    ]
    return pd.DataFrame(
        {
            "trade_date": [day for day in dates if day <= last],
            "risk_temperature": 70.0,
            "sh000300_dd60": -0.06,
            "model_confidence": 90.0,
        }
    )


def _published_state() -> FlexState:
    return FlexState(
        as_of="2026-08-10",
        mode="aggressive",
        core=SleevePos(
            status="open",
            entry_signal_date="2026-08-03",
            entry_date="2026-08-04",
            days_held=4,
            days_remaining=1,
            stage_id="CSI300_CORE_BUY",
            names=["沪深300"],
            weights={"沪深300": 1.0},
            etf_code="510300",
            planned_hold_days=5,
        ),
        satellite=SleevePos(
            status="open",
            entry_signal_date="2026-07-29",
            entry_date="2026-07-30",
            days_held=7,
            days_remaining=1,
            stage_id="CSI300_CORE_BUY",
            names=["通信", "恒生科技"],
            weights={"通信": 0.55, "恒生科技": 0.45},
        ),
    )


def test_same_day_rebuild_does_not_rewrite_published_position_identity() -> None:
    state = advance_positions(
        _risk_through("2026-08-10"),
        None,
        _published_state(),
        mode="aggressive",
        active_stages_fn=lambda _feat: ["FALLING_HARD"],
    )

    assert state.to_dict() == _published_state().to_dict()


def test_new_session_advances_clocks_without_replaying_entry_or_basket() -> None:
    state = advance_positions(
        _risk_through("2026-08-11"),
        None,
        _published_state(),
        mode="aggressive",
        active_stages_fn=lambda _feat: ["CSI300_CORE_BUY"],
    )

    assert state.as_of == "2026-08-11"
    assert state.core.entry_date == "2026-08-04"
    assert state.core.days_held == 5
    assert state.satellite.entry_date == "2026-07-30"
    assert state.satellite.entry_signal_date == "2026-07-29"
    assert state.satellite.names == ["通信", "恒生科技"]
    assert state.satellite.weights == {"通信": 0.55, "恒生科技": 0.45}
    assert state.satellite.days_held == 8
