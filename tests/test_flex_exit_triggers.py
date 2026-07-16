"""Flex satellite/core exit triggers must be explicit and deterministic."""
from __future__ import annotations

from src.core.flex_engine import (
    CORE_HOLD_DAYS,
    SAT_DEFAULT_HOLD,
    SAT_MAX_HOLD,
    SAT_MIN_HOLD,
    FlexState,
    SleevePos,
    _core_should_close,
    _sat_close_meta,
    _sat_should_close,
    build_sleeve_exit_plan,
)


def test_sat_max_hold_always_closes():
    st = FlexState(satellite=SleevePos(status="open", days_held=SAT_MAX_HOLD, stage_id="CSI300_CORE_BUY"))
    meta = _sat_close_meta(st, ["CSI300_CORE_BUY", "HIGH_COOLING"])
    assert meta is not None
    assert meta["close_code"] == "MAX_HOLD"
    assert meta["guaranteed"] is True
    assert _sat_should_close(st, ["CSI300_CORE_BUY"]) is True


def test_sat_event_flip_closes_when_opposite():
    st = FlexState(satellite=SleevePos(status="open", days_held=SAT_MIN_HOLD, stage_id="RISING_HARD"))
    meta = _sat_close_meta(st, ["FALLING_HARD"])
    assert meta is not None
    assert meta["close_code"] == "EVENT_FLIP"
    assert "FALLING_HARD" in meta["flip_stages"]


def test_sat_default_no_stage_closes():
    st = FlexState(satellite=SleevePos(status="open", days_held=SAT_DEFAULT_HOLD, stage_id="CSI300_CORE_BUY"))
    meta = _sat_close_meta(st, [])  # no high/observe stages
    assert meta is not None
    assert meta["close_code"] == "DEFAULT_NO_STAGE"


def test_sat_does_not_close_before_min_without_reason():
    st = FlexState(satellite=SleevePos(status="open", days_held=1, stage_id="CSI300_CORE_BUY"))
    assert _sat_close_meta(st, ["CSI300_CORE_BUY"]) is None
    assert _sat_should_close(st, ["CSI300_CORE_BUY"]) is False


def test_core_max_hold():
    st = FlexState(core=SleevePos(status="open", days_held=CORE_HOLD_DAYS))
    assert _core_should_close(st) is True
    st.core.days_held = CORE_HOLD_DAYS - 1
    assert _core_should_close(st) is False


def test_exit_plan_has_max_path():
    st = FlexState(
        as_of="2026-07-15",
        satellite=SleevePos(
            status="open",
            entry_date="2026-07-14",
            entry_signal_date="2026-07-13",
            days_held=1,
            stage_id="CSI300_CORE_BUY",
            names=["恒生科技", "传媒"],
        ),
    )
    dates = [
        "2026-07-14",
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
        "2026-07-23",
        "2026-07-24",
        "2026-07-27",
    ]
    plan = build_sleeve_exit_plan(st, ["CSI300_CORE_BUY"], trade_dates=dates)
    sat = plan["satellite"]
    assert sat["paths"]["max_signal_date"] == "2026-07-24"
    assert sat["paths"]["max_exec_next_open"] == "2026-07-27"
    assert sat["days_to_max"] == SAT_MAX_HOLD - 1
    assert sat["triggered_close"] is None
