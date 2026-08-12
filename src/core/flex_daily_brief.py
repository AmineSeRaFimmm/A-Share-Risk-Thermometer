"""Authoritative daily Flex strategy brief built from one atomic snapshot."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.core.sector_etf_map import map_sector


BRIEF_SCHEMA_VERSION = 1
DEFAULT_SATELLITE_MIN_HOLD = 3
DEFAULT_SATELLITE_STOP_LOSS = -0.03
DEFAULT_SATELLITE_TAKE_PROFIT = 0.04


def _date(value: Any) -> str | None:
    day = str(value or "")[:10]
    if len(day) == 10 and day[4] == "-" and day[7] == "-":
        return day
    return None


def _trade_dates(trade_calendar: dict[str, Any]) -> list[str]:
    return sorted(
        {
            day
            for value in trade_calendar.get("dates") or []
            if (day := _date(value)) is not None
        }
    )


def _next_trade_date(day: str | None, trade_dates: list[str]) -> str | None:
    if not day:
        return None
    return next((candidate for candidate in trade_dates if candidate > day), None)


def _bar(
    etf_daily_marks: dict[str, Any],
    code: str,
    day: str,
) -> dict[str, Any] | None:
    row = (etf_daily_marks.get("by_code") or {}).get(code) or {}
    bar = (row.get("bars") or {}).get(day)
    return bar if isinstance(bar, dict) else None


def _instrument(
    name: str,
    *,
    weight_in_sleeve: float | None = None,
    portfolio_weight: float | None = None,
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action = action or {}
    mapped = map_sector(name)
    code = str(action.get("etf_code") or mapped.get("etf_code") or "")
    etf_name = str(action.get("etf_name") or mapped.get("etf_name") or "")
    return {
        "name": name,
        "etf_code": code or None,
        "etf_name": etf_name or None,
        "weight_in_sleeve": weight_in_sleeve,
        "portfolio_weight": portfolio_weight,
    }


def _satellite_members(
    flex: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    state = ((flex.get("position_state") or {}).get("satellite") or {})
    names = [str(name) for name in state.get("names") or [] if str(name)]
    weights = state.get("weights") or {}
    raw_weights = {name: float(weights.get(name) or 0.0) for name in names}
    total = sum(weight for weight in raw_weights.values() if weight > 0)
    allocation = float((flex.get("allocation") or {}).get("w_sat") or 0.0)
    members: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in names:
        weight = raw_weights[name] / total if total > 0 else 0.0
        member = _instrument(
            name,
            weight_in_sleeve=weight,
            portfolio_weight=allocation * weight if allocation > 0 else None,
        )
        if not member["etf_code"] or weight <= 0:
            missing.append(name)
        members.append(member)
    if not names:
        missing.append("卫星固定篮子")
    return members, missing


def evaluate_satellite_risk_event(
    flex: dict[str, Any],
    etf_daily_marks: dict[str, Any],
    trade_calendar: dict[str, Any],
) -> dict[str, Any]:
    """Find the first eligible fixed-basket EOD stop/take-profit crossing."""

    state = ((flex.get("position_state") or {}).get("satellite") or {})
    signal_date = _date(state.get("entry_signal_date"))
    entry_date = _date(state.get("entry_date"))
    if str(state.get("status") or "").lower() != "open" or not signal_date:
        return {
            "status": "NOT_APPLICABLE",
            "signal_id": signal_date,
            "reason_cn": "当前没有策略卫星持仓",
        }

    rule = flex.get("satellite_risk_rule") or {}
    exit_rules = (((flex.get("exit_plan") or {}).get("satellite") or {}).get("rules") or {})
    min_hold = int(exit_rules.get("min_hold") or DEFAULT_SATELLITE_MIN_HOLD)
    stop_loss = float(rule.get("stop_loss") or DEFAULT_SATELLITE_STOP_LOSS)
    take_profit = float(rule.get("take_profit") or DEFAULT_SATELLITE_TAKE_PROFIT)
    cost_bps = float(flex.get("transaction_cost_bps_one_way") or 0.0)
    cost_rate = cost_bps / 10_000.0
    members, missing_members = _satellite_members(flex)
    base = {
        "signal_id": signal_date,
        "entry_date": entry_date,
        "members": members,
        "min_hold_days": min_hold,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "transaction_cost_bps_one_way": cost_bps,
        "return_formula": "sum(weight * close / entry_open) / (1 + entry_cost_rate) - 1",
    }
    if not entry_date or missing_members:
        return {
            **base,
            "status": "BLOCKED",
            "blocked_code": "INVALID_FIXED_BASKET",
            "missing": missing_members or ["entry_date"],
            "reason_cn": "卫星固定入场篮子不完整，不能判定首次止盈止损",
        }

    entry_open: dict[str, float] = {}
    missing_entry: list[str] = []
    for member in members:
        code = str(member["etf_code"])
        value = float((_bar(etf_daily_marks, code, entry_date) or {}).get("open") or 0.0)
        if value <= 0:
            missing_entry.append(code)
        else:
            entry_open[code] = value
            member["entry_open"] = value
    if missing_entry:
        return {
            **base,
            "status": "BLOCKED",
            "blocked_code": "MISSING_ENTRY_OPEN",
            "missing": missing_entry,
            "reason_cn": f"{entry_date} 入场开盘价不完整，不能判定首次止盈止损",
        }

    strategy_as_of = _date(flex.get("as_of"))
    marks_as_of = _date(
        etf_daily_marks.get("complete_as_of") or etf_daily_marks.get("as_of")
    )
    checked_through = min(
        [day for day in (strategy_as_of, marks_as_of) if day is not None],
        default=None,
    )
    trade_dates = _trade_dates(trade_calendar)
    sessions = [
        day
        for day in trade_dates
        if entry_date <= day and (checked_through is None or day <= checked_through)
    ]
    if not sessions:
        return {
            **base,
            "status": "BLOCKED",
            "blocked_code": "NO_EOD_SESSION",
            "checked_through": checked_through,
            "reason_cn": "入场后没有可核验的共同 EOD 交易日",
        }

    latest_return: float | None = None
    latest_days_held = 0
    for days_held, day in enumerate(sessions, start=1):
        ratio = 0.0
        missing_close: list[str] = []
        for member in members:
            code = str(member["etf_code"])
            close = float((_bar(etf_daily_marks, code, day) or {}).get("close") or 0.0)
            if close <= 0:
                missing_close.append(code)
                continue
            ratio += float(member["weight_in_sleeve"] or 0.0) * close / entry_open[code]
        if missing_close:
            return {
                **base,
                "status": "BLOCKED",
                "blocked_code": "MISSING_COMMON_EOD",
                "checked_through": sessions[days_held - 2] if days_held > 1 else None,
                "blocked_on": day,
                "missing": missing_close,
                "latest_return": latest_return,
                "reason_cn": f"{day} 固定篮子共同 EOD 不完整，不能越过缺口判定首次触发",
            }
        basket_return = ratio / (1.0 + cost_rate) - 1.0
        latest_return = basket_return
        latest_days_held = days_held
        if days_held < min_hold:
            continue
        if basket_return <= stop_loss or basket_return >= take_profit:
            stop = basket_return <= stop_loss
            execution_date = _next_trade_date(day, trade_dates)
            execution_bar_complete = bool(execution_date) and all(
                float(
                    (_bar(etf_daily_marks, str(member["etf_code"]), execution_date) or {}).get("open")
                    or 0.0
                )
                > 0
                for member in members
            )
            return {
                **base,
                "status": "TRIGGERED",
                "event_type": "STOP_LOSS" if stop else "TAKE_PROFIT",
                "close_code": "LOCAL_STOP_LOSS" if stop else "LOCAL_TAKE_PROFIT",
                "action_cn": "卫星篮子止损" if stop else "卫星篮子止盈",
                "trigger_date": day,
                "execution_date": execution_date,
                "execution_status": (
                    "EXECUTED"
                    if execution_bar_complete
                    and strategy_as_of
                    and execution_date <= strategy_as_of
                    else "PENDING"
                ),
                "execution_bar_complete": execution_bar_complete,
                "trigger_return": basket_return,
                "trigger_threshold": stop_loss if stop else take_profit,
                "days_held": days_held,
                "checked_through": day,
                "reason_cn": (
                    f"固定卫星篮子持有第 {days_held} 日 EOD 收益首次"
                    f"{'低于止损线' if stop else '达到止盈线'}"
                ),
            }

    return {
        **base,
        "status": "CLEAR",
        "checked_through": sessions[-1],
        "latest_return": latest_return,
        "days_held": latest_days_held,
        "reason_cn": "截至共同 EOD 尚未触发卫星止盈止损",
    }


def _execution_fields(
    rows: list[dict[str, Any]],
    signal_date: str,
    trade_dates: list[str],
) -> dict[str, Any]:
    tail = any(str(row.get("execution_mode") or "") == "T_TAIL_1450" for row in rows)
    if tail:
        return {
            "execution_mode": "T_TAIL_1450",
            "execution_date": signal_date,
            "execution_window_cn": "T 日 14:50-15:00",
            "execution_status": "PENDING",
        }
    execution_date = _next_trade_date(signal_date, trade_dates)
    return {
        "execution_mode": "T_PLUS_1_OPEN",
        "execution_date": execution_date,
        "execution_window_cn": "下一交易日开盘",
        "execution_status": "PENDING",
    }


def _group_strategy_actions(
    flex: dict[str, Any],
    trade_dates: list[str],
    *,
    suppress_satellite_close: bool,
) -> list[dict[str, Any]]:
    as_of = _date(flex.get("as_of"))
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for source, event_type in (("buy_list", "ENTRY"), ("close_list", "EXIT")):
        for row in flex.get(source) or []:
            if not isinstance(row, dict):
                continue
            sleeve = str(row.get("sleeve") or "other").lower()
            if suppress_satellite_close and event_type == "EXIT" and sleeve == "satellite":
                continue
            action = str(row.get("action") or row.get("side") or "").upper()
            if event_type == "ENTRY" and action not in {"OPEN", "BUY", "OVERWEIGHT"}:
                continue
            if event_type == "EXIT" and action not in {"CLOSE", "SELL"}:
                continue
            close_code = str(row.get("close_code") or "")
            signal_date = _date(row.get("signal_as_of")) or as_of
            if not signal_date:
                continue
            grouped[(event_type, sleeve, close_code, signal_date)].append(row)

    events: list[dict[str, Any]] = []
    for (event_type, sleeve, close_code, signal_date), rows in grouped.items():
        instruments = [
            _instrument(
                str(row.get("name") or row.get("sector") or "—"),
                portfolio_weight=(
                    float(row.get("weight_target"))
                    if row.get("weight_target") is not None
                    else None
                ),
                action=row,
            )
            for row in rows
        ]
        reasons = list(dict.fromkeys(str(row.get("why") or "") for row in rows if row.get("why")))
        execution = _execution_fields(rows, signal_date, trade_dates)
        action_cn = str(rows[0].get("action_cn") or ("策略入场" if event_type == "ENTRY" else "策略离场"))
        events.append(
            {
                "id": ":".join((signal_date, event_type, sleeve, close_code or "OPEN")),
                "event_type": event_type,
                "sleeve": sleeve,
                "action_cn": action_cn,
                "title_cn": f"{'核心' if sleeve == 'core' else '卫星'}{action_cn}",
                "signal_date": signal_date,
                "close_code": close_code or None,
                "reason_cn": "；".join(reasons),
                "instruments": instruments,
                **execution,
            }
        )
    return events


def build_daily_flex_brief(
    stage_playbook: dict[str, Any],
    etf_daily_marks: dict[str, Any],
    trade_calendar: dict[str, Any],
    intraday_temperature: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one strategy-only report; no browser ledger or inferred action is used."""

    flex = stage_playbook.get("flex_panel") or {}
    intraday_temperature = intraday_temperature or {}
    strategy_as_of = _date(flex.get("as_of") or stage_playbook.get("as_of"))
    tail_summary = intraday_temperature.get("core_tail_day_summary") or {}
    tail_signal = intraday_temperature.get("core_tail_signal") or {}
    tail_date = (
        _date(tail_signal.get("trade_date") or intraday_temperature.get("trade_date"))
        if tail_summary.get("execute_triggered")
        else None
    )
    as_of = max(
        [day for day in (strategy_as_of, tail_date) if day is not None],
        default=None,
    )
    trade_dates = _trade_dates(trade_calendar)
    risk_event = evaluate_satellite_risk_event(flex, etf_daily_marks, trade_calendar)
    risk_triggered = risk_event.get("status") == "TRIGGERED"
    items = _group_strategy_actions(
        flex,
        trade_dates,
        suppress_satellite_close=risk_triggered,
    )
    if risk_triggered:
        event_type = str(risk_event["event_type"])
        executed = risk_event.get("execution_status") == "EXECUTED"
        action_cn = "卫星篮子止损" if event_type == "STOP_LOSS" else "卫星篮子止盈"
        items.append(
            {
                "id": f"{risk_event['trigger_date']}:{event_type}:satellite",
                "event_type": event_type,
                "sleeve": "satellite",
                "action_cn": action_cn,
                "title_cn": f"{action_cn}{'已执行' if executed else '信号'}",
                "signal_date": risk_event["trigger_date"],
                "execution_date": risk_event.get("execution_date"),
                "execution_mode": "T_PLUS_1_OPEN",
                "execution_window_cn": "下一交易日开盘整篮平仓",
                "execution_status": risk_event.get("execution_status"),
                "close_code": risk_event.get("close_code"),
                "trigger_return": risk_event.get("trigger_return"),
                "trigger_threshold": risk_event.get("trigger_threshold"),
                "days_held": risk_event.get("days_held"),
                "reason_cn": risk_event.get("reason_cn"),
                "instruments": risk_event.get("members") or [],
            }
        )

    if tail_date:
        items = [
            item
            for item in items
            if not (
                item.get("event_type") == "ENTRY"
                and item.get("sleeve") == "core"
                and item.get("signal_date") == tail_date
            )
        ]
        sat_state_open = str(
            (((flex.get("position_state") or {}).get("satellite") or {}).get("status") or "")
        ).lower() == "open"
        sat_risk_executed = risk_triggered and risk_event.get("execution_status") == "EXECUTED"
        sat_open = sat_state_open and not sat_risk_executed
        target_weight = 0.6 if sat_open else 1.0
        items.append(
            {
                "id": f"{tail_date}:ENTRY:core:CORE_STRICT_TAIL_V2",
                "event_type": "ENTRY",
                "sleeve": "core",
                "action_cn": "CORE 严格尾盘买入",
                "title_cn": "核心 CORE 严格尾盘买入信号",
                "signal_date": tail_date,
                "execution_date": tail_date,
                "execution_mode": "T_TAIL_1450",
                "execution_window_cn": "T 日 14:50-15:00",
                "execution_status": "SIGNAL_RECORDED",
                "close_code": None,
                "reason_cn": str(
                    tail_signal.get("execution_cn")
                    or "CORE 严格条件连续稳定，并在尾盘窗口再次通过"
                ),
                "instruments": [
                    _instrument(
                        "沪深300",
                        portfolio_weight=target_weight,
                        action={"etf_code": "510300", "etf_name": "沪深300ETF华泰柏瑞"},
                    )
                ],
                "signal_source": "intraday_temperature.core_tail_day_summary",
            }
        )

    priority = {"STOP_LOSS": 0, "TAKE_PROFIT": 0, "EXIT": 1, "ENTRY": 2}
    items.sort(key=lambda item: (priority.get(str(item.get("event_type")), 9), str(item.get("sleeve"))))
    marks_as_of = _date(
        etf_daily_marks.get("complete_as_of") or etf_daily_marks.get("as_of")
    )
    if items:
        status = "ACTION"
        headline = f"截至 {as_of} 有 {len(items)} 项有效策略动作"
    elif any(
        str(((flex.get("position_state") or {}).get(sleeve) or {}).get("status") or "").lower()
        == "open"
        for sleeve in ("core", "satellite")
    ):
        status = "HOLD"
        headline = "今日没有新增入场或离场信号"
    else:
        status = "NO_ACTION"
        headline = "今日没有 Flex 策略动作"

    return {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "strategy_id": "FLEX_AGGRESSIVE",
        "mode": "aggressive",
        "as_of": as_of,
        "marks_as_of": marks_as_of,
        "status": status,
        "headline_cn": headline,
        "items": items,
        "satellite_risk_event": risk_event,
        "data_quality": {
            "strategy_as_of": as_of,
            "marks_as_of": marks_as_of,
            "marks_quality": etf_daily_marks.get("quality"),
            "risk_check_status": risk_event.get("status"),
            "complete": risk_event.get("status") != "BLOCKED",
        },
        "provenance": {
            "source": "FLEX_ENGINE_ATOMIC_SNAPSHOT",
            "action_lists": ["flex_panel.buy_list", "flex_panel.close_list"],
            "browser_ledger_used": False,
        },
    }
