from copy import deepcopy

import pandas as pd
import pytest

from src.core.flex_daily_brief import build_daily_flex_brief, evaluate_satellite_risk_event
from src.core.flex_engine import FlexState, SleevePos, advance_positions, load_position_state, save_position_state


DATES = pd.bdate_range("2026-08-03", periods=9).strftime("%Y-%m-%d").tolist()


def initial():
    return FlexState(as_of=DATES[2], satellite=SleevePos(
        status="open", entry_signal_date=DATES[0], entry_date=DATES[1],
        days_held=2, days_remaining=6, stage_id="RISING_HARD",
        names=["通信"], weights={"通信": 1.0},
    ))


def marks(price=1.05):
    return {"complete_as_of": DATES[-1], "by_code": {"515880": {"bars": {
        d: {"open": 1.0, "close": price if i >= 3 else 1.0}
        for i, d in enumerate(DATES)
    }}}}


def advance(state, last, data, stages=None):
    risk = pd.DataFrame({"trade_date": DATES[:last + 1], "risk_temperature": 50.0})
    return advance_positions(risk, None, state, etf_daily_marks=data,
                             trade_calendar={"dates": DATES},
                             active_stages_fn=lambda _: stages or ["RISING_HARD"])


@pytest.mark.parametrize("price,reason", [(1.05, "TAKE_PROFIT"), (0.96, "STOP_LOSS")])
def test_third_eod_records_next_open_event_and_keeps_fixed_basket(price, reason):
    previous = initial()
    result = advance(previous, 3, marks(price))
    event = result.execution_events[0]
    assert result.satellite.entry_date == previous.satellite.entry_date
    assert result.satellite.weights == previous.satellite.weights
    assert result.satellite.days_held == 3
    assert event["event_type"] == reason
    assert event["execution_status"] == "PENDING"
    assert event["trigger_date"] == DATES[3]
    assert event["execution_date"] == DATES[4]
    assert event["event_id"] and event["signal_id"] == DATES[0]
    assert event["members"][0]["etf_code"] == "515880"
    assert previous.execution_events == []
    assert advance(result, 3, marks(price)).to_dict() == result.to_dict()


def test_exit_session_cooldown_then_next_session_signal_and_following_open():
    pending = advance(initial(), 3, marks())
    exited = advance(pending, 4, marks())
    assert exited.satellite.status == "flat"
    assert exited.execution_events[0]["execution_status"] == "EXECUTED"
    assert exited.cooldown_through["satellite"] == DATES[4]
    reentered = advance(exited, 6, marks())
    assert reentered.satellite.entry_signal_date == DATES[5]
    assert reentered.satellite.entry_date == DATES[6]
    assert len(reentered.execution_events) == 1


def test_pending_exit_requires_valid_common_open_and_is_not_duplicated():
    pending = advance(initial(), 3, marks())
    data = marks()
    data["by_code"]["515880"]["bars"][DATES[4]]["open"] = float("nan")
    blocked = advance(pending, 4, data)
    assert blocked.satellite.status == "open"
    assert len(blocked.execution_events) == 1
    assert blocked.execution_events[0]["execution_status"] == "PENDING"
    filled = advance(blocked, 4, marks())
    assert filled.satellite.status == "flat"
    assert filled.execution_events[0]["execution_status"] == "EXECUTED"


def test_missing_eod_does_not_skip_to_later_crossing():
    data = marks()
    del data["by_code"]["515880"]["bars"][DATES[2]]["close"]
    result = advance(initial(), 3, data)
    assert result.execution_events == []
    assert result.satellite_risk_check["blocked_code"] == "MISSING_COMMON_EOD"


def test_legacy_position_adopts_old_trigger_without_backdating_fill():
    previous = initial()
    previous.as_of = DATES[4]
    previous.satellite.days_held = 4
    result = advance(previous, 4, marks())
    event = result.execution_events[0]
    assert event["historical_trigger_date"] == DATES[3]
    assert event["trigger_date"] == DATES[4]
    assert event["execution_date"] == DATES[5]
    assert event["execution_status"] == "PENDING"
    assert result.satellite.to_dict() == previous.satellite.to_dict()


def test_state_roundtrip_retains_event_history_and_cooldown(tmp_path):
    state = advance(advance(initial(), 3, marks()), 4, marks())
    path = tmp_path / "state.json"
    save_position_state(state, path)
    assert load_position_state(path).to_dict() == state.to_dict()


def test_flat_brief_retains_exit_until_execution_eod_and_archives_afterward():
    state = advance(advance(initial(), 3, marks()), 4, marks())
    panel = {"as_of": DATES[4], "position_state": state.to_dict()}
    brief = build_daily_flex_brief({"flex_panel": panel}, marks(), {"dates": DATES})
    assert brief["items"][0]["execution_status"] == "EXECUTED"
    assert brief["items"][0]["instruments"][0]["etf_code"] == "515880"
    panel["as_of"] = DATES[5]
    later = build_daily_flex_brief({"flex_panel": panel}, {}, {"dates": DATES})
    assert later["items"] == []
    assert len(later["execution_events"]) == 1


def test_incremental_and_batch_advancement_match():
    batch = advance(initial(), 6, marks())
    step = deepcopy(initial())
    for i in range(3, 7):
        step = advance(step, i, marks())
    assert step.to_dict() == batch.to_dict()


def test_fixed_entry_weights_not_daily_rebalanced(monkeypatch):
    import src.core.flex_daily_brief as brief_module

    monkeypatch.setattr(brief_module, "map_sector", lambda name: {"etf_code": name})
    panel = {"as_of": DATES[3], "transaction_cost_bps_one_way": 1.0,
             "position_state": {"satellite": {
                 "status": "open", "entry_signal_date": DATES[0], "entry_date": DATES[1],
                 "names": ["A", "B"], "weights": {"A": 0.5, "B": 0.5},
             }}}
    data = {"complete_as_of": DATES[3], "by_code": {
        "A": {"bars": {DATES[1]: {"open": 100, "close": 100}, DATES[2]: {"close": 200}, DATES[3]: {"close": 100}}},
        "B": {"bars": {DATES[1]: {"open": 100, "close": 100}, DATES[2]: {"close": 100}, DATES[3]: {"close": 100}}},
    }}
    event = evaluate_satellite_risk_event(panel, data, {"dates": DATES})
    assert event["status"] == "CLEAR"
    assert event["latest_return"] == pytest.approx(1 / 1.0001 - 1)


def test_reentered_position_not_hidden_by_old_executed_event():
    state = advance(initial(), 6, marks())
    panel = {"as_of": DATES[6], "position_state": state.to_dict()}
    brief = build_daily_flex_brief({"flex_panel": panel}, {}, {"dates": DATES})
    assert brief["status"] == "HOLD"
    assert len(brief["execution_events"]) == 1
    assert brief["satellite_risk_event"].get("status") != "TRIGGERED"


def test_old_state_without_event_fields_loads_without_rewriting_entry(tmp_path):
    import json

    raw = initial().to_dict()
    for key in ("execution_events", "cooldown_through", "satellite_risk_check"):
        del raw[key]
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_position_state(path)
    assert loaded.satellite.to_dict() == raw["satellite"]
    assert loaded.execution_events == []
    assert loaded.cooldown_through == {}
