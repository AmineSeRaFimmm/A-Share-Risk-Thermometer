from __future__ import annotations

import pytest

from src.core.flex_daily_brief import build_daily_flex_brief


def _calendar() -> dict:
    return {
        "dates": [
            "2026-08-03",
            "2026-08-04",
            "2026-08-05",
            "2026-08-06",
            "2026-08-07",
            "2026-08-10",
            "2026-08-11",
            "2026-08-12",
            "2026-08-13",
        ]
    }


def _playbook(*, close_list: list[dict] | None = None, buy_list: list[dict] | None = None) -> dict:
    return {
        "as_of": "2026-08-10",
        "flex_panel": {
            "as_of": "2026-08-10",
            "mode": "aggressive",
            "transaction_cost_bps_one_way": 1,
            "satellite_risk_rule": {"stop_loss": -0.03, "take_profit": 0.04},
            "allocation": {"w_core": 0.6, "w_sat": 0.4},
            "buy_list": buy_list or [],
            "close_list": close_list or [],
            "position_state": {
                "core": {"status": "flat"},
                "satellite": {
                    "status": "open",
                    "entry_signal_date": "2026-08-03",
                    "entry_date": "2026-08-04",
                    "names": ["通信"],
                    "weights": {"通信": 1.0},
                },
            },
            "exit_plan": {
                "satellite": {
                    "rules": {"min_hold": 3},
                    "paths": {
                        "max_signal_date": "2026-08-10",
                        "max_exec_next_open": "2026-08-11",
                    },
                }
            },
        },
    }


def _marks(closes: dict[str, float]) -> dict:
    bars = {
        "2026-08-04": {"open": 1.0, "close": closes.get("2026-08-04", 1.0)},
    }
    bars.update(
        {
            day: {"open": 1.0 if day == "2026-08-04" else value, "close": value}
            for day, value in closes.items()
        }
    )
    return {
        "as_of": "2026-08-10",
        "complete_as_of": "2026-08-10",
        "quality": "OK",
        "by_code": {"515880": {"etf_code": "515880", "bars": bars}},
    }


def test_first_take_profit_supersedes_later_satellite_max_hold_exit() -> None:
    close_list = [
        {
            "sleeve": "satellite",
            "name": "通信",
            "action": "CLOSE",
            "action_cn": "最长持有到期卖出",
            "close_code": "MAX_HOLD",
            "etf_code": "515880",
            "etf_name": "通信ETF国泰",
            "why": "最长持有到期",
        }
    ]
    brief = build_daily_flex_brief(
        _playbook(close_list=close_list),
        _marks(
            {
                "2026-08-04": 1.01,
                "2026-08-05": 1.02,
                "2026-08-06": 1.041,
                "2026-08-07": 1.06,
                "2026-08-10": 1.08,
            }
        ),
        _calendar(),
    )

    assert brief["satellite_risk_event"]["status"] == "TRIGGERED"
    assert brief["satellite_risk_event"]["trigger_date"] == "2026-08-06"
    assert brief["satellite_risk_event"]["execution_date"] == "2026-08-07"
    assert brief["satellite_risk_event"]["trigger_return"] == pytest.approx(
        1.041 / 1.0001 - 1
    )
    assert brief["items"] == []
    assert not any(item.get("close_code") == "MAX_HOLD" for item in brief["items"])


def test_triggered_risk_event_is_visible_through_execution_day_only() -> None:
    playbook = _playbook()
    playbook["as_of"] = "2026-08-07"
    playbook["flex_panel"]["as_of"] = "2026-08-07"
    marks = _marks(
        {
            "2026-08-04": 1.01,
            "2026-08-05": 1.02,
            "2026-08-06": 1.041,
            "2026-08-07": 1.06,
        }
    )
    marks["as_of"] = "2026-08-07"
    marks["complete_as_of"] = "2026-08-07"

    brief = build_daily_flex_brief(playbook, marks, _calendar())

    assert brief["as_of"] == "2026-08-07"
    assert [item["event_type"] for item in brief["items"]] == ["TAKE_PROFIT"]
    assert brief["visibility_policy"]["expires_next_trade_session"] is True


def test_intraday_trade_date_advances_report_and_expires_old_action() -> None:
    core_close = {
        "sleeve": "core",
        "name": "沪深300",
        "action": "CLOSE",
        "action_cn": "核心到期卖出",
        "close_code": "MAX_HOLD",
        "etf_code": "510300",
        "why": "最长持有到期",
    }
    playbook = _playbook(close_list=[core_close])
    playbook["as_of"] = "2026-08-11"
    playbook["flex_panel"]["as_of"] = "2026-08-11"

    execution_day = build_daily_flex_brief(
        playbook,
        _marks({"2026-08-10": 1.0}),
        _calendar(),
        {"trade_date": "2026-08-12"},
    )

    assert execution_day["as_of"] == "2026-08-12"
    assert execution_day["data_quality"]["strategy_as_of"] == "2026-08-11"
    assert [item["event_type"] for item in execution_day["items"]] == ["EXIT"]

    next_session = build_daily_flex_brief(
        playbook,
        _marks({"2026-08-10": 1.0}),
        _calendar(),
        {"trade_date": "2026-08-13"},
    )

    assert next_session["as_of"] == "2026-08-13"
    assert next_session["items"] == []


def test_missing_common_eod_blocks_first_crossing_instead_of_skipping_gap() -> None:
    marks = _marks(
        {
            "2026-08-04": 1.01,
            "2026-08-06": 1.06,
            "2026-08-07": 1.07,
            "2026-08-10": 1.08,
        }
    )
    marks["by_code"]["515880"]["bars"].pop("2026-08-05", None)
    brief = build_daily_flex_brief(_playbook(), marks, _calendar())

    risk = brief["satellite_risk_event"]
    assert risk["status"] == "BLOCKED"
    assert risk["blocked_code"] == "MISSING_COMMON_EOD"
    assert risk["blocked_on"] == "2026-08-05"
    assert brief["data_quality"]["complete"] is False
    assert not any(item["event_type"] == "TAKE_PROFIT" for item in brief["items"])


def test_strategy_entries_are_grouped_by_sleeve_with_exact_execution_policy() -> None:
    buy_list = [
        {
            "sleeve": "satellite",
            "name": name,
            "action": "OPEN",
            "action_cn": "卫星买入",
            "etf_code": code,
            "weight_target": weight,
            "why": "阶段信号",
        }
        for name, code, weight in [
            ("通信", "515880", 0.24),
            ("传媒", "512980", 0.16),
        ]
    ]
    playbook = _playbook(buy_list=buy_list)
    playbook["flex_panel"]["position_state"]["satellite"] = {"status": "flat"}
    brief = build_daily_flex_brief(playbook, _marks({"2026-08-10": 1.0}), _calendar())

    assert len(brief["items"]) == 1
    entry = brief["items"][0]
    assert entry["event_type"] == "ENTRY"
    assert entry["signal_date"] == "2026-08-10"
    assert entry["execution_date"] == "2026-08-11"
    assert [row["etf_code"] for row in entry["instruments"]] == ["515880", "512980"]


def test_confirmed_core_tail_event_is_published_without_browser_inference() -> None:
    playbook = _playbook()
    playbook["flex_panel"]["position_state"]["satellite"] = {"status": "flat"}
    intraday = {
        "trade_date": "2026-08-11",
        "core_tail_day_summary": {"execute_triggered": True, "execute_at": "2026-08-11T14:52:00+08:00"},
        "core_tail_signal": {
            "trade_date": "2026-08-11",
            "execution_cn": "严格质量条件连续通过，仅核心仓 T 日尾盘执行",
        },
    }
    brief = build_daily_flex_brief(
        playbook,
        _marks({"2026-08-10": 1.0}),
        _calendar(),
        intraday,
    )

    assert brief["as_of"] == "2026-08-11"
    assert len(brief["items"]) == 1
    entry = brief["items"][0]
    assert entry["event_type"] == "ENTRY"
    assert entry["execution_mode"] == "T_TAIL_1450"
    assert entry["execution_date"] == "2026-08-11"
    assert entry["instruments"][0]["portfolio_weight"] == 1.0
    assert entry["signal_source"] == "intraday_temperature.core_tail_day_summary"


def test_core_tail_target_ignores_satellite_state_after_executed_risk_exit() -> None:
    intraday = {
        "trade_date": "2026-08-10",
        "core_tail_day_summary": {"execute_triggered": True},
        "core_tail_signal": {"trade_date": "2026-08-10"},
    }
    brief = build_daily_flex_brief(
        _playbook(),
        _marks(
            {
                "2026-08-04": 1.0,
                "2026-08-05": 1.01,
                "2026-08-06": 1.041,
                "2026-08-07": 1.05,
                "2026-08-10": 1.06,
            }
        ),
        _calendar(),
        intraday,
    )

    assert brief["satellite_risk_event"]["execution_status"] == "EXECUTED"
    core_tail = next(item for item in brief["items"] if item["event_type"] == "ENTRY")
    assert core_tail["instruments"][0]["portfolio_weight"] == 1.0


def test_confirmed_core_tail_replaces_same_day_core_open_instead_of_duplicating() -> None:
    core_open = {
        "sleeve": "core",
        "name": "沪深300",
        "action": "OPEN",
        "action_cn": "核心新开买入",
        "etf_code": "510300",
        "weight_target": 1.0,
        "why": "日线阶段信号",
    }
    playbook = _playbook(buy_list=[core_open])
    playbook["flex_panel"]["position_state"]["satellite"] = {"status": "flat"}
    intraday = {
        "trade_date": "2026-08-10",
        "core_tail_day_summary": {"execute_triggered": True},
        "core_tail_signal": {"trade_date": "2026-08-10"},
    }
    brief = build_daily_flex_brief(
        playbook,
        _marks({"2026-08-10": 1.0}),
        _calendar(),
        intraday,
    )

    entries = [item for item in brief["items"] if item["event_type"] == "ENTRY"]
    assert len(entries) == 1
    assert entries[0]["execution_mode"] == "T_TAIL_1450"
    assert entries[0]["signal_source"] == "intraday_temperature.core_tail_day_summary"
