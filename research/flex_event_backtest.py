"""Chronological Flex account replay, with explicit unverified-data stops.

Industry-index units are fractional research proxies, not exchange ETF fills.
An EOD close must never serve as evidence of an executable intraday signal.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from src.core.flex_execution import (
    EXECUTION_CONTRACT_VERSION, ExecutionTrade, fixed_basket_return,
    positive_price, rebalance_values, satellite_exit_reason,
)
from src.core.flex_engine import (
    CORE_HOLD_DAYS, SAT_DEFAULT_HOLD, SAT_MAX_HOLD, SAT_MIN_HOLD,
    SAT_STOP_LOSS, SAT_TAKE_PROFIT, STAGE_OPPOSITES, STAGE_TIER,
    compute_allocation, merge_satellite_targets, quality_adjusted_return,
)
from src.core.core_tail_policy import core_tail_strict_values_eligible
from src.core.sector_etf_map import map_sector
from research.backtest_core_plus_sectors import detect_stages_row


def simulate_execution(
    df: pd.DataFrame, meta: dict, *, mode: str, cost: float,
    apply_proxy_adjustment: bool, event_exit: bool, start_i: int,
    tail_mode: str = "strict_evidence", enabled_sleeves: tuple[str, ...] = ("core", "satellite"),
) -> dict[str, Any]:
    if tail_mode not in {"strict_evidence", "eod_close_proxy", "t_plus_1"}:
        raise ValueError("unknown tail evidence protocol")
    n = len(df)
    days = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d").tolist()
    opens = {"core": df["csi_open"].to_numpy(float), **meta["sector_open"]}
    closes = {"core": df["csi_close"].to_numpy(float), **meta["sector_close"]}
    cash = 1.0
    cycles: dict[str, dict] = {}
    positions: dict[str, dict] = {}
    orders: dict[str, dict] = {}
    cooldown: dict[str, int] = {}
    trades: list[ExecutionTrade] = []
    events: list[dict] = []
    issues: list[dict] = []
    blocked = False
    nav = np.ones(n)
    returns = np.zeros(n)
    turnover = np.zeros(n)
    contributions = {s: np.zeros(n) for s in ("core", "satellite")}
    exposure = np.zeros(n)
    fees_total = 0.0
    tail_candidates = 0
    tail_verified = 0

    def raw_price(name: str, i: int, phase: str, override: dict | None = None) -> float | None:
        if override is not None:
            return positive_price(override.get(name))
        values = opens if phase == "open" else closes
        series = values.get(name)
        return None if series is None else positive_price(series[i])

    def model_price(cycle: dict, name: str, price: float) -> float:
        if not apply_proxy_adjustment or cycle["sleeve"] == "core":
            return price
        entry = cycle["entry_prices"][name]
        adjusted = quality_adjusted_return(price / entry - 1, str(map_sector(name).get("quality")))
        result = positive_price(entry * (1 + adjusted))
        if result is None:
            raise ValueError("proxy stress produces nonpositive price")
        return result

    def issue(i: int, phase: str, name: str, reason: str) -> None:
        nonlocal blocked
        blocked = True
        item = {"date": days[i], "phase": phase, "code": name, "reason": reason}
        if item not in issues:
            issues.append(item)

    def equity() -> float:
        return cash + sum(p["qty"] * p["price"] for p in positions.values())

    def mark(i: int, phase: str, override: dict | None = None) -> bool:
        quotes = {key: raw_price(p["name"], i, phase, override) for key, p in positions.items()}
        missing = [key for key, price in quotes.items() if price is None]
        for key in missing:
            issue(i, phase, positions[key]["name"], "MISSING_HELD_PRICE")
        for key, raw in quotes.items():
            if raw is None:
                continue
            p = positions[key]
            cycle = cycles[p["sleeve"]]
            price = model_price(cycle, p["name"], raw)
            pnl = p["qty"] * (price - p["price"])
            cycle["gross_pnl"] += pnl
            contributions[p["sleeve"]][i] += pnl
            p["price"] = price
        return not missing

    def transition(i: int, phase: str, pending: dict, override: dict | None = None) -> None:
        nonlocal cash, fees_total
        if not pending or blocked:
            return
        if not mark(i, phase, override):
            return
        for sleeve, order in pending.items():
            if order["action"] != "ENTRY":
                continue
            for name in order["weights"]:
                if raw_price(name, i, phase, override) is None:
                    issue(i, phase, name, "MISSING_EXECUTION_PRICE")
        if blocked:
            return
        exiting = {s: cycles[s] for s, order in pending.items() if order["action"] == "EXIT" and s in cycles}
        for sleeve, order in pending.items():
            if order["action"] == "ENTRY":
                cycles[sleeve] = {
                    **order, "sleeve": sleeve, "entry_i": i,
                    "entry_prices": {name: raw_price(name, i, phase, override) for name in order["weights"]},
                    "gross_pnl": 0.0, "fees": 0.0, "capital_in": 0.0,
                    "execution_mode": "T_TAIL_1450" if phase == "tail" else "T_PLUS_1_OPEN",
                }
        active = {s: c for s, c in cycles.items() if s not in exiting}
        alloc = compute_allocation("core" in active, "satellite" in active, mode)
        sleeve_weights = {"core": alloc["w_core"], "satellite": alloc["w_sat"]}
        if active.get("satellite", {}).get("observe_only"):
            sleeve_weights["satellite"] *= 0.25
        targets = {
            f"{s}:{name}": sleeve_weights[s] * weight
            for s, cycle in active.items() for name, weight in cycle["weights"].items()
        }
        current = {key: p["qty"] * p["price"] for key, p in positions.items()}
        before = equity()
        desired, expected_fees = rebalance_values(before, current, targets, cost)
        deltas = {key: desired.get(key, 0.0) - current.get(key, 0.0) for key in set(current) | set(targets)}
        actual_fees = 0.0
        for key in sorted(deltas, key=lambda key: (deltas[key], key)):
            amount = deltas[key]
            if abs(amount) < 1e-14:
                continue
            sleeve, name = key.split(":", 1)
            cycle = cycles[sleeve]
            raw = raw_price(name, i, phase, override)
            price = model_price(cycle, name, raw)
            fee = abs(amount) * cost
            old_qty = positions.get(key, {}).get("qty", 0.0)
            qty = max(0.0, old_qty + amount / price)
            cash -= amount + fee
            cycle["fees"] += fee
            if amount > 0:
                cycle["capital_in"] += amount + fee
            contributions[sleeve][i] -= fee
            actual_fees += fee
            turnover[i] += abs(amount) / before if before else 0.0
            events.append({
                "event_id": f"{cycle['signal_id']}:{sleeve}:{days[i]}:{phase}:{name}",
                "signal_id": cycle["signal_id"], "sleeve": sleeve, "date": days[i],
                "phase": phase, "name": name, "side": "BUY" if amount > 0 else "SELL",
                "quantity": abs(amount) / price, "price": price, "raw_price": raw,
                "gross": abs(amount), "fee": fee,
                "reason": pending.get(sleeve, {}).get("reason", "ALLOCATION_CHANGE"),
            })
            if qty > 1e-14:
                positions[key] = {"sleeve": sleeve, "name": name, "qty": qty, "price": price}
            else:
                positions.pop(key, None)
        if cash < -1e-10 or not math.isclose(actual_fees, expected_fees, abs_tol=1e-10):
            raise AssertionError("cash/fee reconciliation failed")
        cash = max(0.0, cash)
        fees_total += actual_fees
        for sleeve, cycle in exiting.items():
            denominator = cycle["capital_in"]
            net = cycle["gross_pnl"] - cycle["fees"]
            trades.append(ExecutionTrade(
                sleeve=sleeve, entry_i=cycle["entry_i"], exit_i=i,
                entry_date=pd.Timestamp(days[cycle["entry_i"]]), exit_date=pd.Timestamp(days[i]),
                ret=net / denominator if denominator else 0.0,
                observe_only=cycle.get("observe_only", False),
                gross_ret=cycle["gross_pnl"] / denominator if denominator else 0.0,
                gross_pnl=cycle["gross_pnl"], net_pnl=net, fees=cycle["fees"], capital_in=denominator,
                signal_id=cycle["signal_id"], exit_reason=pending[sleeve]["reason"],
                execution_mode=cycle["execution_mode"],
            ))
            del cycles[sleeve]
            cooldown[sleeve] = i

    for i in range(start_i, n):
        previous_nav = nav[i - 1] if i > start_i else 1.0
        mark(i, "open")
        pending = orders
        orders = {}
        transition(i, "open", pending)
        row = df.iloc[i]
        stages = detect_stages_row(row)
        core_signal = bool(pd.notna(row.get("dd60")) and 60 <= row["rt"] < 80 and row["dd60"] <= -0.05)
        core_available = "core" in enabled_sleeves and "core" not in cycles and cooldown.get("core", -1) < i
        strict_alpha = core_signal and core_tail_strict_values_eligible(
            risk_temperature=row["rt"], hs300_drawdown_60d=row.get("dd60"), model_confidence=row.get("model_confidence"))
        tail_order = None
        tail_prices = None
        evidence = meta.get("tail_evidence", {}).get(days[i], {})
        evidence_alpha = evidence.get("eligible") and core_tail_strict_values_eligible(
            risk_temperature=evidence.get("risk_temperature"),
            hs300_drawdown_60d=evidence.get("hs300_drawdown_60d"),
            model_confidence=evidence.get("model_confidence"),
        )
        if core_available and (strict_alpha or (tail_mode == "strict_evidence" and evidence_alpha)):
            tail_candidates += 1
            # Strict mode requires a point-in-time alpha decision AND executable
            # prices for every asset touched by the allocation change.
            if tail_mode == "strict_evidence" and evidence_alpha:
                decided = pd.to_datetime(evidence.get("decision_at"), errors="coerce")
                fill = pd.to_datetime(evidence.get("fill_at"), errors="coerce")
                if pd.notna(decided) and pd.notna(fill) and decided.tzinfo and fill.tzinfo:
                    decided, fill = decided.tz_convert("Asia/Shanghai"), fill.tz_convert("Asia/Shanghai")
                    valid = (
                        decided.strftime("%Y-%m-%d") == days[i] == fill.strftime("%Y-%m-%d")
                        and "14:50" <= decided.strftime("%H:%M") < "15:00"
                        and decided <= fill and fill.strftime("%H:%M") < "15:00"
                        and evidence.get("sample_count", 0) >= 3
                        and evidence.get("stable_minutes", 0) >= 15
                        and evidence.get("point_in_time_verified") is True
                    )
                    required = {"core"} | {p["name"] for p in positions.values()}
                    prices = evidence.get("prices") or {}
                    if valid and all(positive_price(prices.get(name)) for name in required):
                        tail_prices = prices
                        tail_verified += 1
            elif tail_mode == "eod_close_proxy":
                tail_prices = {name: raw_price(name, i, "close") for name in {"core"} | {p["name"] for p in positions.values()}}
            if tail_prices is not None:
                tail_order = {"core": {"action": "ENTRY", "reason": "CORE_TAIL", "signal_id": days[i], "weights": {"core": 1.0}, "stage": "CSI300_CORE_BUY", "hold_days": CORE_HOLD_DAYS + 1}}
        if tail_order:
            transition(i, "tail", tail_order, tail_prices)
        mark(i, "close")
        nav[i] = equity()
        returns[i] = nav[i] / previous_nav - 1
        exposure[i] = sum(p["qty"] * p["price"] for p in positions.values()) / nav[i] if nav[i] else 0.0
        for sleeve in contributions:
            contributions[sleeve][i] /= previous_nav
        if not math.isclose(sum(contributions[s][i] for s in contributions), returns[i], abs_tol=1e-9):
            raise AssertionError("sleeve P&L does not reconcile to account")
        if blocked:
            continue
        for sleeve, cycle in cycles.items():
            held = i - cycle["entry_i"] + 1
            reason = None
            if sleeve == "core" and held >= cycle["hold_days"]:
                reason = "CORE_HOLD_COMPLETE"
            if sleeve == "satellite":
                prices = {name: model_price(cycle, name, raw_price(name, i, "close")) for name in cycle["weights"]}
                ret = fixed_basket_return(cycle["weights"], cycle["entry_prices"], prices, entry_cost_rate=cost)
                reason = satellite_exit_reason(held, ret, min_hold=SAT_MIN_HOLD, stop_loss=SAT_STOP_LOSS, take_profit=SAT_TAKE_PROFIT)
                if not reason and event_exit:
                    if held >= SAT_MAX_HOLD:
                        reason = "MAX_HOLD"
                    elif held >= SAT_MIN_HOLD and STAGE_OPPOSITES.get(cycle["stage"], set()).intersection(stages):
                        reason = "EVENT_FLIP"
                    elif held >= SAT_DEFAULT_HOLD and not any(STAGE_TIER.get(s) in {"high", "observe"} for s in stages):
                        reason = "DEFAULT_NO_STAGE"
                elif not reason and held >= SAT_DEFAULT_HOLD and not event_exit:
                    reason = "DEFAULT_HOLD"
            if reason:
                orders[sleeve] = {"action": "EXIT", "reason": reason, "signal_id": days[i]}
        if core_available and "core" not in cycles and core_signal:
            orders["core"] = {"action": "ENTRY", "reason": "CORE_SIGNAL", "signal_id": days[i], "weights": {"core": 1.0}, "stage": "CSI300_CORE_BUY", "hold_days": CORE_HOLD_DAYS}
        if "satellite" in enabled_sleeves and "satellite" not in cycles and cooldown.get("satellite", -1) < i:
            longs, _, _ = merge_satellite_targets(stages, rising_hard="RISING_HARD" in stages)
            high = any(STAGE_TIER.get(s) == "high" for s in stages)
            observe = any(STAGE_TIER.get(s) == "observe" for s in stages)
            if longs and (high or observe):
                selected = longs if high else longs[:1]
                weights = {item["name"]: float(item["weight_in_sat"]) for item in selected}
                total = sum(weights.values())
                primary = next(s for s in ["CSI300_CORE_BUY", "HIGH_COOLING", "ENTER_70_BOUNCE", "RISING_HARD", "FALLING_HARD"] if s in stages)
                orders["satellite"] = {"action": "ENTRY", "reason": "SATELLITE_SIGNAL", "signal_id": days[i], "weights": {name: weight / total for name, weight in weights.items()}, "stage": primary, "observe_only": not high}
    return {
        "portfolio_daily": returns, "core_daily": contributions["core"], "sat_daily": contributions["satellite"],
        "turnover_daily": turnover, "exposure_daily": exposure, "equity_daily": nav,
        "core_trades": [t for t in trades if t.sleeve == "core"],
        "sat_trades": [t for t in trades if t.sleeve == "satellite"], "trades": trades,
        "events": events, "open_positions": positions, "pending_orders": orders, "cash": cash,
        "fees_total": fees_total, "start_i": start_i,
        "execution_quality": {
            "status": "INCOMPLETE" if issues else "COMPLETE", "issues": issues,
            "blocked_from": issues[0]["date"] if issues else None,
            "contract_version": EXECUTION_CONTRACT_VERSION,
            "asset_basis": "industry_index_fractional_proxy", "tail_mode": tail_mode,
            "tail_candidates": tail_candidates, "verified_tail_entries": tail_verified,
            "proxy_adjustment": "cumulative_entry_relative_stress" if apply_proxy_adjustment else "none",
        },
    }
