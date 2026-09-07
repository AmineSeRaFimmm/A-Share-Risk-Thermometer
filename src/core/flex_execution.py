"""Price and accounting contracts shared by Flex execution and research.

No temperature or signal selection rules belong in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping


EXECUTION_CONTRACT_VERSION = "flex-events-v1"


def positive_price(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def fixed_basket_return(
    weights: Mapping[str, float],
    entry_prices: Mapping[str, float],
    current_prices: Mapping[str, float],
    *,
    entry_cost_rate: float = 0.0001,
) -> float | None:
    """Entry-weighted cumulative return, never a daily rebalanced index."""
    if not weights or not math.isfinite(entry_cost_rate) or entry_cost_rate < 0:
        return None
    total = 0.0
    gross = 0.0
    for name, raw_weight in weights.items():
        weight = positive_price(raw_weight)
        entry = positive_price(entry_prices.get(name))
        current = positive_price(current_prices.get(name))
        if weight is None or entry is None or current is None:
            return None
        total += weight
        gross += weight * current / entry
    return gross / total / (1.0 + entry_cost_rate) - 1.0


def satellite_exit_reason(
    days_held: int,
    basket_return: float | None,
    *,
    min_hold: int = 3,
    stop_loss: float = -0.03,
    take_profit: float = 0.04,
) -> str | None:
    if days_held < min_hold or basket_return is None or not math.isfinite(basket_return):
        return None
    if basket_return <= stop_loss:
        return "STOP_LOSS"
    if basket_return >= take_profit:
        return "TAKE_PROFIT"
    return None


def rebalance_values(
    equity: float,
    current_values: Mapping[str, float],
    target_weights: Mapping[str, float],
    cost_rate: float,
) -> tuple[dict[str, float], float]:
    """Solve target NAV after fees; callers execute sales before purchases."""
    if not math.isfinite(equity) or equity < 0 or not 0 <= cost_rate < 1:
        raise ValueError("invalid capital or transaction cost")
    if any(not math.isfinite(w) or w < 0 for w in target_weights.values()):
        raise ValueError("invalid target weight")
    if sum(target_weights.values()) > 1 + 1e-9:
        raise ValueError("target exposure exceeds available capital")
    keys = set(current_values) | set(target_weights)
    low, high = 0.0, equity
    for _ in range(60):
        nav = (low + high) / 2
        fee = cost_rate * sum(
            abs(target_weights.get(key, 0.0) * nav - current_values.get(key, 0.0))
            for key in keys
        )
        if nav + fee > equity:
            high = nav
        else:
            low = nav
    values = {key: weight * low for key, weight in target_weights.items()}
    fee = cost_rate * sum(abs(values.get(key, 0.0) - current_values.get(key, 0.0)) for key in keys)
    return values, fee


@dataclass
class ExecutionTrade:
    sleeve: str
    entry_i: int
    exit_i: int
    entry_date: object
    exit_date: object
    ret: float
    observe_only: bool = False
    gross_ret: float | None = None
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    fees: float = 0.0
    capital_in: float = 0.0
    signal_id: str = ""
    exit_reason: str = ""
    execution_mode: str = "T_PLUS_1_OPEN"
