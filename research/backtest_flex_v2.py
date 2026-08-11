#!/usr/bin/env python3
"""Flex v2 production backtest.

This is the source for data/calculated/flex_backtest_stats.json.
The production contract is:
  - strict CORE enters in the T-day tail (T close proxy); all other signals use T+1 open
  - daily mark path uses the real open/close path, not endpoint smoothing
  - portfolio costs are charged from target-weight turnover, including rebalances
  - observe-only satellite sleeves use the same 0.25 size scale as production
  - proxy ETF realism discounts gains and amplifies losses
  - the historical split is a retrospective holdout, not parameter-independent OOS
  - expanding fixed-policy windows test temporal stability without relabeling it independence
  - prospective validation is frozen from 2026-08-12 onward
"""
from __future__ import annotations

import json
import hashlib
import inspect
import math
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.backtest_core_plus_sectors import (  # noqa: E402
    OOS_SPLIT,
    TRADING_DAYS,
    annualized,
    core_signal,
    detect_stages_row,
    load_aligned,
    max_dd,
)
from src.core.flex_engine import (  # noqa: E402
    CORE_HOLD_DAYS,
    FLEX_SAT_LONG,
    FLEX_SAT_SHORT,
    MODE_AGGRESSIVE,
    MODE_CONSERVATIVE,
    QUALITY_WEIGHT,
    SAT_DEFAULT_HOLD,
    SAT_MAX_HOLD,
    SAT_MIN_HOLD,
    SAT_STOP_LOSS,
    SAT_TAKE_PROFIT,
    STAGE_MERGE_SCORE,
    STAGE_OPPOSITES,
    STAGE_TIER,
    SIZING,
    merge_satellite_targets,
    quality_adjusted_return,
)
from src.core.core_tail_policy import (  # noqa: E402
    core_tail_policy_payload,
    core_tail_strict_values_eligible,
)
from src.core.sector_etf_map import map_sector  # noqa: E402
from src.storage.paths import CALCULATED  # noqa: E402

OUT = ROOT / "research/output/core_plus_sectors"
OBSERVE_SCALE = 0.25
WALK_FORWARD_MIN_TRAIN = 504
WALK_FORWARD_TEST_DAYS = 252
WALK_FORWARD_MIN_TEST = 126
PROSPECTIVE_START = pd.Timestamp("2026-08-12")
FROZEN_POLICY_FINGERPRINT = "764ec74d1e8aeb2ec0a9610d36b04e4844c59220b22d1872646477360e276d46"


@dataclass
class Trade:
    sleeve: str
    entry_i: int
    exit_i: int
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    ret: float
    observe_only: bool = False


def quality_of(name: str) -> str:
    return str(map_sector(name).get("quality") or "missing")


def _safe_ret(a: float, b: float) -> float:
    if not (np.isfinite(a) and np.isfinite(b) and a > 0 and b > 0):
        return 0.0
    return float(b / a - 1.0)


def instrument_path_returns(
    opens: np.ndarray,
    closes: np.ndarray,
    entry_i: int,
    exit_i: int,
    *,
    name: str | None = None,
    apply_proxy_adjustment: bool = False,
) -> dict[int, float] | None:
    """Return day-indexed raw path from entry open to exit open."""
    n = len(opens)
    if entry_i >= n or exit_i >= n or entry_i < 0 or exit_i <= entry_i:
        return None
    if not (np.isfinite(opens[entry_i]) and opens[entry_i] > 0):
        return None
    path: dict[int, float] = {}
    path[entry_i] = _safe_ret(float(opens[entry_i]), float(closes[entry_i]))
    for j in range(entry_i + 1, exit_i):
        path[j] = _safe_ret(float(closes[j - 1]), float(closes[j]))
    path[exit_i] = _safe_ret(float(closes[exit_i - 1]), float(opens[exit_i]))

    if apply_proxy_adjustment and name:
        q = quality_of(name)
        path = {j: quality_adjusted_return(r, q) for j, r in path.items()}
    return path


def instrument_next_open_returns(
    opens: np.ndarray,
    closes: np.ndarray,
    entry_i: int,
    exit_i: int,
    *,
    name: str | None = None,
    apply_proxy_adjustment: bool = False,
) -> dict[int, float]:
    """Candidate close-to-next-open returns for EOD-triggered exits."""
    gaps = {
        j: _safe_ret(float(closes[j - 1]), float(opens[j]))
        for j in range(entry_i + 1, min(exit_i, len(opens) - 1) + 1)
    }
    if apply_proxy_adjustment and name:
        q = quality_of(name)
        gaps = {j: quality_adjusted_return(r, q) for j, r in gaps.items()}
    return gaps


def instrument_tail_close_path_returns(
    opens: np.ndarray,
    closes: np.ndarray,
    signal_i: int,
    exit_i: int,
) -> dict[int, float] | None:
    """Enter at T close while preserving the original T+1 strategy exit date."""
    if signal_i < 0 or exit_i >= len(opens) or exit_i <= signal_i + 1:
        return None
    entry = float(closes[signal_i])
    if not np.isfinite(entry) or entry <= 0:
        return None
    path: dict[int, float] = {signal_i: 0.0}
    for j in range(signal_i + 1, exit_i):
        path[j] = _safe_ret(float(closes[j - 1]), float(closes[j]))
    path[exit_i] = _safe_ret(float(closes[exit_i - 1]), float(opens[exit_i]))
    return path


def _path_total(path: dict[int, float]) -> float:
    if not path:
        return 0.0
    return float(np.prod([1.0 + r for _, r in sorted(path.items())]) - 1.0)


def sleeve_stats(daily: np.ndarray, trades: list[Trade], label: str, start_i: int = 0) -> dict:
    d = daily[start_i:]
    equity = np.cumprod(1.0 + d) if len(d) else np.array([])
    total = float(equity[-1] - 1.0) if len(equity) else 0.0
    rets = [t.ret for t in trades if t.entry_i >= start_i]
    return {
        "label": label,
        "total_return": total,
        "ann_return": annualized(total, len(d)),
        "max_dd": max_dd(equity) if len(equity) else float("nan"),
        "trade_count": len(rets),
        "win_rate": float(np.mean([r > 0 for r in rets])) if rets else float("nan"),
        "avg_trade": float(np.mean(rets)) if rets else float("nan"),
        "exposure_ratio": float(np.mean(np.abs(d) > 1e-12)) if len(d) else 0.0,
        "sharpe": float(np.mean(d) / np.std(d, ddof=1) * math.sqrt(TRADING_DAYS))
        if len(d) > 2 and np.std(d, ddof=1) > 0
        else float("nan"),
    }


def _allocation(core_on: bool, sat_on: bool, sat_observe: bool, mode: str) -> tuple[float, float]:
    cfg = SIZING[mode]
    w_core = float(cfg["core_when_signal"]) if core_on else 0.0
    w_sat = float(cfg["sat_when_signal"]) if sat_on else 0.0
    if cfg.get("flex_single_full"):
        if core_on and not sat_on:
            w_core, w_sat = 1.0, 0.0
        elif sat_on and not core_on:
            w_core, w_sat = 0.0, 1.0
    if sat_observe and w_sat > 0:
        w_sat *= OBSERVE_SCALE
    total = w_core + w_sat
    cap = float(cfg["total_cap"])
    if total > cap > 0:
        w_core *= cap / total
        w_sat *= cap / total
    return w_core, w_sat


def _sat_exit_i(df: pd.DataFrame, entry_i: int, primary: str, n: int, event_exit: bool) -> int:
    exit_i = min(entry_i + SAT_DEFAULT_HOLD, n - 1)
    if not event_exit:
        return exit_i
    for k in range(entry_i + SAT_MIN_HOLD, min(entry_i + SAT_MAX_HOLD, n - 1) + 1):
        st_sig = detect_stages_row(df.iloc[k - 1]) if k - 1 >= 0 else detect_stages_row(df.iloc[min(k, n - 1)])
        held = k - entry_i
        if held >= SAT_MIN_HOLD and STAGE_OPPOSITES.get(primary, set()).intersection(st_sig):
            return k
        if held >= SAT_MAX_HOLD:
            return k
        if held >= SAT_DEFAULT_HOLD and not any(STAGE_TIER.get(s) in {"high", "observe"} for s in st_sig):
            return k
    return min(entry_i + SAT_MAX_HOLD, n - 1)


def _apply_sat_risk_exit(
    path: dict[int, float],
    next_open_path: dict[int, float],
    entry_i: int,
    planned_exit_i: int,
) -> tuple[dict[int, float], int]:
    """Detect on an EOD close and execute at the next available open."""
    cum = 1.0
    for j in range(entry_i, planned_exit_i):
        cum *= 1.0 + path.get(j, 0.0)
        held = j - entry_i + 1
        if held < SAT_MIN_HOLD:
            continue
        ret = cum - 1.0
        if ret <= SAT_STOP_LOSS or ret >= SAT_TAKE_PROFIT:
            execution_i = j + 1
            if execution_i not in next_open_path:
                continue
            realized = {k: v for k, v in path.items() if k <= j}
            realized[execution_i] = next_open_path[execution_i]
            return realized, execution_i
    return path, planned_exit_i


def _simulate(
    df: pd.DataFrame,
    meta: dict,
    *,
    mode: str,
    cost: float,
    apply_proxy_adjustment: bool,
    event_exit: bool,
    start_i: int,
) -> dict:
    n = len(df)
    dates = df["trade_date"]
    csi_open = df["csi_open"].to_numpy(dtype=float)
    csi_close = df["csi_close"].to_numpy(dtype=float)
    sector_open = meta["sector_open"]
    sector_close = meta["sector_close"]

    core_daily = np.zeros(n, dtype=float)
    sat_daily = np.zeros(n, dtype=float)
    core_active = np.zeros(n, dtype=bool)
    sat_active = np.zeros(n, dtype=bool)
    sat_observe = np.zeros(n, dtype=bool)
    core_trades: list[Trade] = []
    sat_trades: list[Trade] = []

    next_free = start_i
    for i in range(start_i, n - 2):
        if i < next_free:
            continue
        if not core_signal(df.iloc[i]):
            continue
        entry_i = i + 1
        exit_i = entry_i + CORE_HOLD_DAYS
        if exit_i >= n:
            # Do not turn a still-open tail position into a completed trade.
            continue
        row = df.iloc[i]
        tail_entry = core_tail_strict_values_eligible(
            risk_temperature=row.get("rt"),
            hs300_drawdown_60d=row.get("dd60"),
            model_confidence=row.get("model_confidence"),
        )
        actual_entry_i = i if tail_entry else entry_i
        path = (
            instrument_tail_close_path_returns(csi_open, csi_close, i, exit_i)
            if tail_entry
            else instrument_path_returns(csi_open, csi_close, entry_i, exit_i)
        )
        if not path:
            continue
        for j, r in path.items():
            core_daily[j] = r
            core_active[j] = True
        core_trades.append(
            Trade(
                "core",
                actual_entry_i,
                exit_i,
                pd.Timestamp(dates.iloc[actual_entry_i]),
                pd.Timestamp(dates.iloc[exit_i]),
                _path_total(path),
            )
        )
        next_free = exit_i + 1

    i = start_i
    while i < n - 2:
        stages = detect_stages_row(df.iloc[i])
        rising = "RISING_HARD" in stages
        longs, _av, _sup = merge_satellite_targets(list(stages), rising_hard=rising)
        high = [s for s in stages if STAGE_TIER.get(s) == "high"]
        obs = [s for s in stages if STAGE_TIER.get(s) == "observe"]
        if not longs or (not high and not obs):
            i += 1
            continue
        observe_only = not high and bool(obs)
        if observe_only:
            longs = longs[:1]
        use = [x for x in longs if x["name"] in sector_open and QUALITY_WEIGHT.get(quality_of(x["name"]), 0) > 0]
        if not use:
            i += 1
            continue
        primary = next(
            (s for s in ["CSI300_CORE_BUY", "HIGH_COOLING", "ENTER_70_BOUNCE", "RISING_HARD", "FALLING_HARD"] if s in stages),
            stages[0],
        )
        entry_i = i + 1
        if entry_i + SAT_MAX_HOLD >= n:
            # Require the complete policy window; otherwise the trade is
            # right-censored and cannot enter return/win-rate statistics.
            i += 1
            continue
        exit_i = _sat_exit_i(df, entry_i, primary, n, event_exit)

        paths = []
        next_open_paths = []
        weights = []
        for x in use:
            p = instrument_path_returns(
                sector_open[x["name"]],
                sector_close[x["name"]],
                entry_i,
                exit_i,
                name=x["name"],
                apply_proxy_adjustment=apply_proxy_adjustment,
            )
            if p:
                paths.append(p)
                next_open_paths.append(
                    instrument_next_open_returns(
                        sector_open[x["name"]],
                        sector_close[x["name"]],
                        entry_i,
                        exit_i,
                        name=x["name"],
                        apply_proxy_adjustment=apply_proxy_adjustment,
                    )
                )
                weights.append(max(float(x.get("weight_in_sat") or 0.0), 1e-6))
        if not paths:
            i += 1
            continue
        w = np.asarray(weights, dtype=float)
        w = w / w.sum()
        basket_path = {
            j: float(sum(w[k] * paths[k].get(j, 0.0) for k in range(len(paths))))
            for j in range(entry_i, exit_i + 1)
        }
        basket_next_open = {
            j: float(sum(w[k] * next_open_paths[k].get(j, 0.0) for k in range(len(next_open_paths))))
            for j in range(entry_i + 1, exit_i + 1)
        }
        basket_path, exit_i = _apply_sat_risk_exit(
            basket_path, basket_next_open, entry_i, exit_i
        )
        for j, r in basket_path.items():
            sat_daily[j] = r
            sat_active[j] = True
            sat_observe[j] = observe_only
        trade_ret = _path_total(basket_path)
        sat_trades.append(
            Trade(
                "satellite",
                entry_i,
                exit_i,
                pd.Timestamp(dates.iloc[entry_i]),
                pd.Timestamp(dates.iloc[exit_i]),
                trade_ret,
                observe_only,
            )
        )
        i = exit_i + 1

    port = np.zeros(n, dtype=float)
    prev_core = 0.0
    prev_sat = 0.0
    turnover = np.zeros(n, dtype=float)
    for j in range(start_i, n):
        w_core, w_sat = _allocation(bool(core_active[j]), bool(sat_active[j]), bool(sat_observe[j]), mode)
        traded = abs(w_core - prev_core) + abs(w_sat - prev_sat)
        turnover[j] = traded
        port[j] = w_core * core_daily[j] + w_sat * sat_daily[j] - traded * cost
        prev_core, prev_sat = w_core, w_sat

    trades = core_trades + sat_trades
    return {
        "core_daily": core_daily,
        "sat_daily": sat_daily,
        "portfolio_daily": port,
        "turnover_daily": turnover,
        "core_trades": core_trades,
        "sat_trades": sat_trades,
        "trades": trades,
        "start_i": start_i,
    }


def _slice_meta(meta: dict, start_i: int, end_i: int) -> dict:
    return {
        **meta,
        "sector_open": {name: values[start_i:end_i] for name, values in meta["sector_open"].items()},
        "sector_close": {name: values[start_i:end_i] for name, values in meta["sector_close"].items()},
    }


def _fixed_policy_walk_forward(
    df: pd.DataFrame,
    meta: dict,
    *,
    mode: str,
    cost: float,
    apply_proxy_adjustment: bool,
    event_exit: bool,
) -> dict:
    """Expanding-calendar, non-overlapping forward tests of the frozen policy."""
    folds = []
    stitched_daily: list[float] = []
    stitched_trades: list[Trade] = []
    start_i = WALK_FORWARD_MIN_TRAIN
    while len(df) - start_i >= WALK_FORWARD_MIN_TEST:
        end_i = min(start_i + WALK_FORWARD_TEST_DAYS, len(df))
        frame = df.iloc[start_i:end_i].reset_index(drop=True)
        result = _simulate(
            frame,
            _slice_meta(meta, start_i, end_i),
            mode=mode,
            cost=cost,
            apply_proxy_adjustment=apply_proxy_adjustment,
            event_exit=event_exit,
            start_i=0,
        )
        stats = sleeve_stats(result["portfolio_daily"], result["trades"], "walk_forward_fold", 0)
        folds.append(
            {
                "train_through": str(pd.Timestamp(df.iloc[start_i - 1]["trade_date"]).date()),
                "test_start": str(pd.Timestamp(frame.iloc[0]["trade_date"]).date()),
                "test_end": str(pd.Timestamp(frame.iloc[-1]["trade_date"]).date()),
                "test_days": len(frame),
                "total_return": stats["total_return"],
                "ann_return": stats["ann_return"],
                "max_dd": stats["max_dd"],
                "sharpe": stats["sharpe"],
                "trade_count": stats["trade_count"],
                "win_rate": stats["win_rate"],
            }
        )
        stitched_daily.extend(result["portfolio_daily"].tolist())
        stitched_trades.extend(result["trades"])
        start_i = end_i
    aggregate = sleeve_stats(
        np.asarray(stitched_daily, dtype=float), stitched_trades, "walk_forward_fixed_policy", 0
    ) if stitched_daily else {}
    return {
        "protocol": "expanding calendar; frozen production policy; fresh flat state in each non-overlapping test window",
        "parameter_selection": "none inside folds",
        "independent_parameter_validation": False,
        "purpose": "temporal stability only; stage definitions were researched retrospectively",
        "folds": folds,
        "aggregate": aggregate,
    }


def _prospective_validation(
    df: pd.DataFrame,
    meta: dict,
    *,
    mode: str,
    cost: float,
    apply_proxy_adjustment: bool,
    event_exit: bool,
) -> dict:
    start_i = int(np.searchsorted(
        df["trade_date"].to_numpy(dtype="datetime64[ns]"), np.datetime64(PROSPECTIVE_START)
    ))
    actual_fingerprint = _policy_fingerprint()
    base = {
        "policy_frozen_through": "2026-08-11",
        "start": str(PROSPECTIVE_START.date()),
        "protocol": "append-only future observations; no retrospective parameter changes",
        "independent_parameter_validation": True,
        "policy_fingerprint": actual_fingerprint,
        "expected_policy_fingerprint": FROZEN_POLICY_FINGERPRINT,
    }
    if actual_fingerprint != FROZEN_POLICY_FINGERPRINT:
        return {**base, "status": "BLOCKED_POLICY_CHANGED", "sample_days": 0}
    if start_i >= len(df):
        return {**base, "status": "PENDING_NO_FUTURE_SAMPLE", "sample_days": 0}
    result = _simulate(
        df,
        meta,
        mode=mode,
        cost=cost,
        apply_proxy_adjustment=apply_proxy_adjustment,
        event_exit=event_exit,
        start_i=start_i,
    )
    return {
        **base,
        "status": "ACTIVE",
        "sample_days": len(df) - start_i,
        "stats": sleeve_stats(result["portfolio_daily"], result["trades"], "prospective", start_i),
    }


def _policy_fingerprint() -> str:
    from src.core.stage_trade_playbook import STAGE_DEFS

    policy = {
        "core_hold_days": CORE_HOLD_DAYS,
        "sat_hold_days": [SAT_MIN_HOLD, SAT_DEFAULT_HOLD, SAT_MAX_HOLD],
        "sat_risk": [SAT_STOP_LOSS, SAT_TAKE_PROFIT],
        "sizing": SIZING,
        "stage_tier": STAGE_TIER,
        "stage_merge_score": STAGE_MERGE_SCORE,
        "stage_opposites": {key: sorted(value) for key, value in STAGE_OPPOSITES.items()},
        "sat_long": FLEX_SAT_LONG,
        "sat_short": FLEX_SAT_SHORT,
        "stage_definitions": [item for item in STAGE_DEFS if item.get("stage_id") in FLEX_SAT_LONG],
        "implementation": {
            "core_signal": inspect.getsource(core_signal),
            "core_tail_gate": inspect.getsource(core_tail_strict_values_eligible),
            "stage_detection": inspect.getsource(detect_stages_row),
            "target_merge": inspect.getsource(merge_satellite_targets),
            "sat_risk_exit": inspect.getsource(_apply_sat_risk_exit),
            "allocation": inspect.getsource(_allocation),
            "simulation": inspect.getsource(_simulate),
        },
    }
    encoded = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def backtest_v2(
    df: pd.DataFrame,
    meta: dict,
    *,
    buy_cost: float,
    sell_cost: float,
    mode: str,
    apply_haircut: bool = True,
    event_exit: bool = True,
) -> dict:
    """Run full sample, retrospective holdout, and forward validation protocols."""
    cost = max(float(buy_cost), float(sell_cost))
    full = _simulate(
        df,
        meta,
        mode=mode,
        cost=cost,
        apply_proxy_adjustment=apply_haircut,
        event_exit=event_exit,
        start_i=0,
    )
    oos_i = int(np.searchsorted(df["trade_date"].to_numpy(dtype="datetime64[ns]"), np.datetime64(OOS_SPLIT)))
    oos = _simulate(
        df,
        meta,
        mode=mode,
        cost=cost,
        apply_proxy_adjustment=apply_haircut,
        event_exit=event_exit,
        start_i=oos_i,
    )
    walk_forward = _fixed_policy_walk_forward(
        df,
        meta,
        mode=mode,
        cost=cost,
        apply_proxy_adjustment=apply_haircut,
        event_exit=event_exit,
    )
    prospective = _prospective_validation(
        df,
        meta,
        mode=mode,
        cost=cost,
        apply_proxy_adjustment=apply_haircut,
        event_exit=event_exit,
    )

    return {
        "core": sleeve_stats(full["core_daily"], full["core_trades"], "core", 0),
        "satellite": sleeve_stats(full["sat_daily"], full["sat_trades"], "satellite", 0),
        "portfolio": sleeve_stats(full["portfolio_daily"], full["trades"], f"flex_{mode}", 0),
        "oos_portfolio": sleeve_stats(oos["portfolio_daily"], oos["trades"], "oos", oos_i),
        "oos_core": sleeve_stats(oos["core_daily"], oos["core_trades"], "oos_core", oos_i),
        "walk_forward": walk_forward,
        "prospective": prospective,
        "turnover": {
            "full": float(np.sum(full["turnover_daily"])),
            "oos": float(np.sum(oos["turnover_daily"][oos_i:])),
            "cost_model": "target_weight_turnover * one_way_cost",
        },
        "params": {
            "buy_cost": buy_cost,
            "sell_cost": sell_cost,
            "rebalance_cost": cost,
            "mode": mode,
            "apply_proxy_adjustment": apply_haircut,
            "event_exit": event_exit,
            "path_model": "daily_open_close_path",
            "core_tail_policy": core_tail_policy_payload(),
            "core_tail_price_proxy": "T close proxies the executable 14:50-15:00 fill",
            "oos_protocol": f"retrospective holdout starts flat on {OOS_SPLIT.date()}; parameters are not independent",
        },
    }


def pack_stats(r: dict) -> dict:
    p = r["portfolio"]
    o = r["oos_portfolio"]
    return {
        "full_sample": {
            "total_return": p["total_return"],
            "ann_return": p["ann_return"],
            "max_dd": p["max_dd"],
            "win_rate": p["win_rate"],
            "trade_count": p["trade_count"],
            "sharpe": p.get("sharpe"),
            "turnover": r["turnover"]["full"],
        },
        "oos": {
            "total_return": o["total_return"],
            "ann_return": o["ann_return"],
            "max_dd": o["max_dd"],
            "win_rate": o["win_rate"],
            "trade_count": o["trade_count"],
            "turnover": r["turnover"]["oos"],
            "label": "retrospective_holdout",
            "independent_parameter_validation": False,
        },
        "walk_forward": r["walk_forward"],
        "prospective": r["prospective"],
        "core": {
            "total_return": r["core"]["total_return"],
            "ann_return": r["core"]["ann_return"],
            "max_dd": r["core"]["max_dd"],
            "win_rate": r["core"]["win_rate"],
            "trade_count": r["core"]["trade_count"],
        },
        "satellite": {
            "total_return": r["satellite"]["total_return"],
            "ann_return": r["satellite"]["ann_return"],
            "max_dd": r["satellite"]["max_dd"],
            "win_rate": r["satellite"]["win_rate"],
            "trade_count": r["satellite"]["trade_count"],
        },
    }


def main() -> None:
    warnings.filterwarnings("ignore")
    print("Loading aligned data...")
    df, meta = load_aligned()
    df = df.sort_values("trade_date").reset_index(drop=True)
    print(f"n={len(df)} {df.trade_date.min().date()} → {df.trade_date.max().date()}")

    scenarios = []
    for mode in (MODE_CONSERVATIVE, MODE_AGGRESSIVE):
        for bps, label in ((1, "base_1bps"), (15, "stress_15bps"), (30, "stress_30bps")):
            cost = bps / 10000.0
            r = backtest_v2(df, meta, buy_cost=cost, sell_cost=cost, mode=mode, apply_haircut=True, event_exit=True)
            pack = pack_stats(r)
            scenarios.append({"mode": mode, "cost_label": label, "bps": bps, **pack, "params": r["params"]})
            print(
                f"{mode} {label}: ann={pack['full_sample']['ann_return']:.2%} "
                f"dd={pack['full_sample']['max_dd']:.2%} win={pack['full_sample']['win_rate']:.1%} "
                f"n={pack['full_sample']['trade_count']} oos_ann={pack['oos']['ann_return']:.2%}"
            )

    def find(mode: str, label: str) -> dict:
        return next(s for s in scenarios if s["mode"] == mode and s["cost_label"] == label)

    cons = find(MODE_CONSERVATIVE, "base_1bps")
    agg = find(MODE_AGGRESSIVE, "base_1bps")
    core_only = {
        "total_return": cons["core"]["total_return"],
        "ann_return": cons["core"]["ann_return"],
        "max_dd": cons["core"]["max_dd"],
        "win_rate": cons["core"]["win_rate"],
        "trade_count": cons["core"]["trade_count"],
    }

    out = {
        "mode": "combined_flex_v2",
        "validation_status": "GENERATED_VERIFIED",
        "label_cn": "组合 Flex v2（日度路径+换仓成本+代理亏损惩罚）",
        "default_mode": MODE_AGGRESSIVE,
        "hold_days_core": CORE_HOLD_DAYS,
        "hold_days_sat": f"{SAT_MIN_HOLD}-{SAT_MAX_HOLD}",
        "satellite_stop_loss": SAT_STOP_LOSS,
        "satellite_take_profit": SAT_TAKE_PROFIT,
        "execution": "CORE严格条件 T日14:50尾盘；其余信号 T+1开盘",
        "backtest_protocol": {
            "price_path": "entry open → daily close path → exit open; no endpoint smoothing",
            "right_censoring": "signals without a complete maximum execution window inside the sample are excluded",
            "core_tail": "strict CORE uses T close as 14:50-15:00 fill proxy; original exit date is unchanged",
            "core_tail_quality": "live PASS/FAIL/INVALID gate is operational only; historical EOD confidence is not used as a live-quality proxy",
            "cost": "target-weight turnover × one-way bps; entries, exits and rebalances all counted",
            "proxy": "proxy gains are discounted; proxy losses are amplified by the same factor",
            "observe": "observe-only satellite sleeve uses 0.25 production scale",
            "satellite_risk_exit": (
                f"after {SAT_MIN_HOLD} completed sessions, detect basket return <= {SAT_STOP_LOSS:.0%} "
                f"or >= {SAT_TAKE_PROFIT:.0%} at EOD and execute at next open including the gap"
            ),
            "oos": f"retrospective holdout starts flat on {OOS_SPLIT.date()}; not parameter-independent",
            "walk_forward": "expanding fixed-policy temporal-stability windows; no in-fold tuning",
            "prospective": "policy frozen through 2026-08-11; independent observations begin 2026-08-12",
        },
        "core_only": core_only,
        "conservative": {
            "note": "对照口径；总暴露 capped；同一日度路径与成本模型",
            "full_sample": cons["full_sample"],
            "oos": cons["oos"],
            "walk_forward": cons["walk_forward"],
            "prospective": cons["prospective"],
        },
        "aggressive": {
            "note": "生产进取模式；单仓满仓、双仓60/40；卫星-3%止损/+4%止盈；含换仓成本",
            "full_sample": agg["full_sample"],
            "oos": agg["oos"],
            "walk_forward": agg["walk_forward"],
            "prospective": agg["prospective"],
        },
        "cost_stress": {
            "base_bps_one_way": 1,
            "stress_15bps": {
                MODE_CONSERVATIVE: find(MODE_CONSERVATIVE, "stress_15bps")["full_sample"],
                MODE_AGGRESSIVE: find(MODE_AGGRESSIVE, "stress_15bps")["full_sample"],
            },
            "stress_30bps": {
                MODE_CONSERVATIVE: find(MODE_CONSERVATIVE, "stress_30bps")["full_sample"],
                MODE_AGGRESSIVE: find(MODE_AGGRESSIVE, "stress_30bps")["full_sample"],
            },
            "etf_haircut_note": "proxy 正收益折扣、负收益放大 / weak 剔除；行业指数≠ETF",
        },
        "caveat_cn": "板块用行业指数代理；弱代理不进默认篮子；卫星按-3%止损/+4%止盈；回测已计入日度路径、换仓成本和代理亏损惩罚。",
        "scenarios": [
            {
                "mode": s["mode"],
                "cost_label": s["cost_label"],
                "ann_return": s["full_sample"]["ann_return"],
                "max_dd": s["full_sample"]["max_dd"],
                "win_rate": s["full_sample"]["win_rate"],
                "trade_count": s["full_sample"]["trade_count"],
                "turnover": s["full_sample"].get("turnover"),
            }
            for s in scenarios
        ],
    }

    CALCULATED.mkdir(parents=True, exist_ok=True)
    path = CALCULATED / "flex_backtest_stats.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "flex_v2_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote", path)


if __name__ == "__main__":
    main()
