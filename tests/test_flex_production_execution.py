from copy import deepcopy

import pandas as pd
import pytest

from src.core import flex_engine, stage_trade_playbook
from src.core.flex_snapshot import publish_flex_snapshot
from src.storage.json_store import read_json, write_json


DATES = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10"]


@pytest.fixture
def production(tmp_path, monkeypatch):
    site, docs = tmp_path / "site", tmp_path / "docs"
    site.mkdir()
    docs.mkdir()
    state_path = tmp_path / "flex_position_state.json"
    monkeypatch.setattr(flex_engine, "POSITION_STATE_PATH", state_path)
    monkeypatch.setattr(stage_trade_playbook, "SITE", site)
    state = flex_engine.FlexState(as_of=DATES[2], mode="aggressive", satellite=flex_engine.SleevePos(
        status="open", entry_signal_date=DATES[0], entry_date=DATES[1],
        days_held=2, days_remaining=6, stage_id="RISING_HARD",
        names=["通信"], weights={"通信": 1.0},
    ))
    flex_engine.save_position_state(state)
    marks = {"complete_as_of": DATES[4], "by_code": {"515880": {"bars": {
        day: {"open": None if i == 4 else 1.0, "close": 1.05 if i >= 3 else 1.0}
        for i, day in enumerate(DATES)
    }}}}
    write_json(marks, site / "etf_daily_marks.json")
    write_json({"dates": DATES}, site / "trade_calendar.json")
    return site, docs, state_path, marks


def build(site, through):
    risk = pd.DataFrame({"trade_date": DATES[:through + 1], "risk_temperature": 50.0})
    original = risk.copy(deep=True)
    payload = stage_trade_playbook.build_playbook_payload(risk, None)
    pd.testing.assert_frame_equal(risk, original)
    write_json(payload, site / "stage_playbook.json")
    return payload


def test_production_build_and_same_asof_price_repair_refresh_all_outputs(production):
    site, docs, state_path, marks = production
    first = build(site, 3)
    event = first["flex_panel"]["execution_events"][0]
    assert event["trigger_date"] == DATES[3]
    assert event["execution_date"] == DATES[4]
    assert event["execution_status"] == "PENDING"

    before = build(site, 4)
    snapshot = publish_flex_snapshot(site_dir=site, docs_dir=docs)
    assert snapshot["stage_playbook"]["flex_panel"]["position_state"]["satellite"]["status"] == "open"
    assert snapshot["stage_playbook"]["flex_panel"]["execution_events"][0]["execution_status"] == "PENDING"
    published_features = deepcopy(before["market_state"])

    # This is the update_etf_eod_marks publication path: no strategy rebuild.
    marks["by_code"]["515880"]["bars"][DATES[4]]["open"] = 1.07
    write_json(marks, site / "etf_daily_marks.json")
    after = publish_flex_snapshot(site_dir=site, docs_dir=docs)
    panel = after["stage_playbook"]["flex_panel"]
    filled = panel["execution_events"][0]
    assert filled["event_id"] == event["event_id"]
    assert filled["position"] == event["position"]
    assert filled["execution_status"] == "EXECUTED"
    assert filled["execution_basis"] == "ETF_OPEN_CONFIRMED"
    assert panel["position_state"]["satellite"]["status"] == "flat"
    assert panel["cooldown_through"]["satellite"] == DATES[4]
    assert not any(row["sleeve"] == "satellite" for row in panel["buy_list"])
    assert after["strategy_as_of"] == DATES[4]
    assert after["stage_playbook"]["market_state"] == published_features
    assert after["stage_playbook"]["flex_panel"]["backtest"] == before["flex_panel"]["backtest"]
    assert read_json(state_path) == panel["position_state"]
    assert read_json(site / "stage_playbook.json") == after["stage_playbook"]
    assert read_json(docs / "data" / "stage_playbook.json") == after["stage_playbook"]
    assert read_json(docs / "data" / "flex_snapshot.json") == after
    assert after["daily_strategy_brief"]["items"][0]["execution_status"] == "EXECUTED"
    assert publish_flex_snapshot(site_dir=site, docs_dir=docs)["revision"] == after["revision"]


def test_marks_only_refresh_never_advances_strategy_or_counts_sessions(production):
    site, docs, _, marks = production
    payload = build(site, 3)
    marks["by_code"]["515880"]["bars"][DATES[4]]["open"] = 1.07
    write_json(marks, site / "etf_daily_marks.json")
    snapshot = publish_flex_snapshot(site_dir=site, docs_dir=docs)
    state = snapshot["stage_playbook"]["flex_panel"]["position_state"]
    assert state["as_of"] == DATES[3]
    assert state["satellite"]["days_held"] == 3
    assert state["execution_events"][0]["execution_status"] == "PENDING"
    assert snapshot["stage_playbook"]["market_state"] == payload["market_state"]


def test_missing_marks_never_executes_even_legacy_calendar_order():
    state = flex_engine.FlexState(as_of=DATES[3], satellite=flex_engine.SleevePos(
        status="open", entry_signal_date=DATES[0], entry_date=DATES[1],
        days_held=8, names=["通信"], weights={"通信": 1.0},
    ))
    risk = pd.DataFrame({"trade_date": DATES[:5], "risk_temperature": 50.0})
    pending = flex_engine.advance_positions(risk, None, state, active_stages_fn=lambda _: [])
    event = pending.execution_events[0]
    assert event["execution_status"] == "PENDING"
    assert event["execution_blocked_code"] == "MISSING_EXECUTION_OPEN"
    event["requires_open_marks"] = False
    later = flex_engine.advance_positions(
        pd.DataFrame({"trade_date": DATES, "risk_temperature": 50.0}),
        None, pending, active_stages_fn=lambda _: [],
    )
    assert later.satellite.status == "open"
    assert later.execution_events[0]["execution_status"] == "PENDING"


def test_publish_invalid_marks_has_no_persistence_side_effect(production):
    site, docs, state_path, _ = production
    build(site, 3)
    original = state_path.read_bytes()
    write_json({}, site / "etf_daily_marks.json")
    with pytest.raises(ValueError, match="by_code"):
        publish_flex_snapshot(site_dir=site, docs_dir=docs)
    assert state_path.read_bytes() == original
    assert not (site / "flex_snapshot.json").exists()
