#!/usr/bin/env python3
"""Flex v2 production backtest.

This is the source for data/calculated/flex_backtest_stats.json.
The production contract is:
  - strict evidence is required for tail entries; absent evidence uses T+1 open
  - daily mark path uses the real open/close path, not endpoint smoothing
  - portfolio costs are charged from target-weight turnover, including rebalances
  - observe-only satellite sleeves use the same 0.25 size scale as production
  - raw industry-index proxies are the baseline, not executable ETF performance
  - proxy return adjustment and T close tail fills are separate assumption scenarios
  - the historical split is a retrospective holdout, not parameter-independent OOS
  - expanding fixed-policy windows test temporal stability without relabeling it independence
  - prospective validation is blocked until a point-in-time archive exists
"""
from __future__ import annotations

import json
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
from src.core.flex_validation import (  # noqa: E402
    SCHEMA_VERSION,
    blocked_prospective,
    build_input_versions,
    build_policy_manifest,
    build_run_manifest,
    fingerprint,
    json_safe,
)

OUT = ROOT / "research/output/core_plus_sectors"
OBSERVE_SCALE = 0.25
WALK_FORWARD_MIN_TRAIN = 504
WALK_FORWARD_TEST_DAYS = 252
WALK_FORWARD_MIN_TEST = 126
# No historical freeze is inherited by this corrected execution contract.
FROZEN_POLICY_FINGERPRINT = None


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
        raise ValueError("unobservable price return; missing is not zero")
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
    required = np.r_[opens[entry_i], closes[entry_i:exit_i], opens[exit_i]]
    if not np.isfinite(required).all() or (required <= 0).any():
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
        j: (float(opens[j]) / float(closes[j - 1]) - 1.0)
        if np.isfinite(closes[j - 1]) and np.isfinite(opens[j]) and closes[j - 1] > 0 and opens[j] > 0
        else float("nan")
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
    required = np.r_[closes[signal_i:exit_i], opens[exit_i]]
    if not np.isfinite(required).all() or (required <= 0).any():
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
    gross_rets = [getattr(t, "gross_ret", None) for t in trades if t.entry_i >= start_i]
    gross_rets = [r for r in gross_rets if r is not None and np.isfinite(r)]
    return {
        "label": label,
        "total_return": total,
        "ann_return": annualized(total, len(d)),
        "max_dd": max_dd(equity) if len(equity) else float("nan"),
        "trade_count": len(rets),
        "win_rate": float(np.mean([r > 0 for r in rets])) if rets else float("nan"),
        "net_win_rate": float(np.mean([r > 0 for r in rets])) if rets else None,
        "gross_win_rate": float(np.mean([r > 0 for r in gross_rets])) if gross_rets else None,
        "closed_trade_count": len(rets),
        "win_rate_unit": "sleeve_round_trip",
        "return_basis": "net_account_pnl_over_cumulative_purchase_cash",
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
        if j not in path or not np.isfinite(path[j]):
            raise ValueError("incomplete satellite close path")
        cum *= 1.0 + path[j]
        held = j - entry_i + 1
        if held < SAT_MIN_HOLD:
            continue
        ret = cum - 1.0
        if ret <= SAT_STOP_LOSS or ret >= SAT_TAKE_PROFIT:
            execution_i = j + 1
            if execution_i not in next_open_path or not np.isfinite(next_open_path[execution_i]):
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
    tail_mode: str = "strict_evidence",
    enabled_sleeves: tuple[str, ...] = ("core", "satellite"),
) -> dict:
    from research.flex_event_backtest import simulate_execution

    return simulate_execution(
        df, meta, mode=mode, cost=cost,
        apply_proxy_adjustment=apply_proxy_adjustment,
        event_exit=event_exit, start_i=start_i,
        tail_mode=tail_mode, enabled_sleeves=enabled_sleeves,
    )


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
    tail_mode: str = "strict_evidence",
    buy_cost: float | None = None,
    sell_cost: float | None = None,
) -> dict:
    """Non-overlapping retrospective stability windows, not independent OOS."""
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
            tail_mode=tail_mode,
        )
        stats = sleeve_stats(result["portfolio_daily"], result["trades"], "walk_forward_fold", 0)
        folds.append(
            {
                **stats,
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
                "execution_quality": result.get("execution_quality", {"status": "UNKNOWN", "issues": []}),
            }
        )
        stitched_daily.extend(result["portfolio_daily"].tolist())
        stitched_trades.extend(result["trades"])
        start_i = end_i
    aggregate = sleeve_stats(
        np.asarray(stitched_daily, dtype=float), stitched_trades, "walk_forward_fixed_policy", 0
    ) if stitched_daily else {}
    quality = {
        "status": "COMPLETE" if folds and all(f["execution_quality"].get("status") == "COMPLETE" for f in folds) else "INCOMPLETE",
        "issues": [issue for f in folds for issue in f["execution_quality"].get("issues", [])],
        "tail_mode": tail_mode,
    }
    return {
        "protocol": "fixed current policy replay; fresh flat state in each non-overlapping historical test window",
        "parameter_selection": "none inside folds",
        "independent_parameter_validation": False,
        "purpose": "temporal stability only; stage definitions were researched retrospectively",
        "folds": [_pack_metric_block(f, f["execution_quality"]) for f in folds],
        "aggregate": _pack_metric_block(aggregate, quality),
        "execution_quality": quality,
    }


def _prospective_validation(
    df: pd.DataFrame,
    meta: dict,
    *,
    mode: str,
    cost: float,
    apply_proxy_adjustment: bool,
    event_exit: bool,
    tail_mode: str = "strict_evidence",
    buy_cost: float | None = None,
    sell_cost: float | None = None,
) -> dict:
    return blocked_prospective(
        policy_fingerprint=_policy_fingerprint(),
        scenario={
            "mode": mode,
            "buy_cost": cost if buy_cost is None else buy_cost,
            "sell_cost": cost if sell_cost is None else sell_cost,
            "apply_proxy_adjustment": apply_proxy_adjustment,
            "event_exit": event_exit,
            "tail_mode": tail_mode,
        },
    )


def _policy_fingerprint() -> str:
    return build_policy_manifest()["policy_fingerprint"]


def _execution_diagnostics(result: dict) -> dict:
    equity = result.get("equity_daily", [])
    return json_safe({
        "diagnostic_only": True,
        "valuation_verified": (result.get("execution_quality") or {}).get("status") == "COMPLETE",
        "ending_equity_diagnostic": equity[-1] if len(equity) else None,
        "cash": result.get("cash"),
        "fees_total": result.get("fees_total"),
        "open_positions": result.get("open_positions", {}),
        "pending_orders": result.get("pending_orders", {}),
        "completed_trade_count": len(result.get("trades", [])),
        "event_count": len(result.get("events", [])),
        "event_fingerprint": fingerprint({"events": result.get("events", [])}),
    })


def backtest_v2(
    df: pd.DataFrame,
    meta: dict,
    *,
    buy_cost: float,
    sell_cost: float,
    mode: str,
    apply_haircut: bool = False,
    event_exit: bool = True,
    tail_mode: str = "strict_evidence",
) -> dict:
    """Run retrospective replay; strict prospective validation remains blocked."""
    if not (np.isfinite(buy_cost) and np.isfinite(sell_cost)) or min(buy_cost, sell_cost) < 0:
        raise ValueError("Trading costs must be finite and non-negative")
    if float(buy_cost) != float(sell_cost):
        raise ValueError("The account simulator currently requires equal buy and sell costs")
    cost = float(buy_cost)
    full = _simulate(
        df,
        meta,
        mode=mode,
        cost=cost,
        apply_proxy_adjustment=apply_haircut,
        event_exit=event_exit,
        start_i=0,
        tail_mode=tail_mode,
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
        tail_mode=tail_mode,
    )
    walk_forward = _fixed_policy_walk_forward(
        df,
        meta,
        mode=mode,
        cost=cost,
        apply_proxy_adjustment=apply_haircut,
        event_exit=event_exit,
        tail_mode=tail_mode,
        buy_cost=buy_cost,
        sell_cost=sell_cost,
    )
    prospective = _prospective_validation(
        df,
        meta,
        mode=mode,
        cost=cost,
        apply_proxy_adjustment=apply_haircut,
        event_exit=event_exit,
        tail_mode=tail_mode,
        buy_cost=buy_cost,
        sell_cost=sell_cost,
    )

    full_quality = full.get("execution_quality", {"status": "UNKNOWN", "issues": []})
    oos_quality = oos.get("execution_quality", {"status": "UNKNOWN", "issues": []})
    return {
        "core": _pack_metric_block(sleeve_stats(full["core_daily"], full["core_trades"], "core", 0), full_quality, contribution=True),
        "satellite": _pack_metric_block(sleeve_stats(full["sat_daily"], full["sat_trades"], "satellite", 0), full_quality, contribution=True),
        "portfolio": _pack_metric_block(sleeve_stats(full["portfolio_daily"], full["trades"], f"flex_{mode}", 0), full_quality),
        "oos_portfolio": _pack_metric_block(sleeve_stats(oos["portfolio_daily"], oos["trades"], "oos", oos_i), oos_quality),
        "oos_core": _pack_metric_block(sleeve_stats(oos["core_daily"], oos["core_trades"], "oos_core", oos_i), oos_quality, contribution=True),
        "walk_forward": walk_forward,
        "prospective": prospective,
        "execution_quality": {
            "full": full_quality,
            "oos": oos_quality,
        },
        "execution_diagnostics": {"full": _execution_diagnostics(full), "oos": _execution_diagnostics(oos)},
        "turnover": {
            "full": float(np.sum(full["turnover_daily"])),
            "oos": float(np.sum(oos["turnover_daily"][oos_i:])),
            "cost_model": "account_fill_notional * side_cost",
        },
        "params": {
            "buy_cost": buy_cost,
            "sell_cost": sell_cost,
            "rebalance_cost": cost,
            "mode": mode,
            "apply_proxy_adjustment": apply_haircut,
            "event_exit": event_exit,
            "tail_mode": tail_mode,
            "path_model": "event_driven_account_ledger",
            "instrument_basis": "industry_index_proxy_not_executable_etf",
            "core_tail_policy": core_tail_policy_payload(),
            "core_tail_price_proxy": (
                "hypothetical T close fill; not verified executable tail evidence"
                if tail_mode == "eod_close_proxy" else
                "strict evidence required; missing historical tail evidence defers to T+1 open"
            ),
            "oos_protocol": f"retrospective holdout starts flat on {OOS_SPLIT.date()}; parameters are not independent",
        },
    }


def _pack_metric_block(stats: dict, quality: dict | None, *, contribution: bool = False) -> dict:
    quality = quality or {"status": "UNKNOWN", "issues": []}
    complete = quality.get("status") == "COMPLETE"
    net_win = stats.get("net_win_rate", stats.get("win_rate"))
    packed = {
        **stats,
        "net_win_rate": net_win,
        "gross_win_rate": stats.get("gross_win_rate"),
        "win_rate": net_win,
        "win_rate_basis": "net_after_costs",
        "gross_win_rate_basis": "before_costs_after_scenario_proxy_adjustment",
        "win_rate_unit": "closed_sleeve_round_trip",
        "trade_return_basis": "net_account_pnl_over_cumulative_purchase_cash",
        "trade_count_scope": "completed_trades_only; open positions excluded; incomplete replay counts are diagnostic",
        "return_basis": "net_account_sleeve_contribution" if contribution else "net_account_return",
        "performance_valid": complete,
        "validation_status": "EXECUTION_COMPLETE_REPLAY" if complete else "INCOMPLETE_EXECUTION",
        "independent_parameter_validation": False,
        "execution_quality": quality,
    }
    if contribution:
        packed["standalone_strategy"] = False
        # Compounding a sleeve's portfolio contribution is not standalone NAV.
        for key in ("total_return", "ann_return", "max_dd", "sharpe"):
            packed[key] = None
    if not complete:
        for key in (
            "total_return", "ann_return", "max_dd", "sharpe", "win_rate",
            "net_win_rate", "gross_win_rate", "avg_trade", "gross_avg_trade",
            "net_avg_trade", "exposure_ratio",
        ):
            packed[key] = None
    return json_safe(packed)


def pack_stats(r: dict) -> dict:
    qualities = r.get("execution_quality") or {}
    full_quality = qualities.get("full")
    oos_quality = qualities.get("oos")
    walk_forward = dict(r["walk_forward"])
    walk_forward["aggregate"] = _pack_metric_block(
        walk_forward.get("aggregate") or {}, walk_forward.get("execution_quality"),
    )
    walk_forward["folds"] = [
        _pack_metric_block(fold, fold.get("execution_quality"))
        for fold in walk_forward.get("folds", [])
    ]
    return {
        "full_sample": {
            **_pack_metric_block(r["portfolio"], full_quality),
            "turnover": r["turnover"]["full"],
        },
        "oos": {
            **_pack_metric_block(r["oos_portfolio"], oos_quality),
            "turnover": r["turnover"]["oos"],
            "label": "retrospective_holdout",
        },
        "walk_forward": walk_forward,
        "prospective": r["prospective"],
        "execution_diagnostics": r.get("execution_diagnostics", {}),
        "core": _pack_metric_block(r["core"], full_quality, contribution=True),
        "satellite": _pack_metric_block(r["satellite"], full_quality, contribution=True),
    }


def main() -> None:
    print("Loading aligned data...")
    policy = build_policy_manifest()
    inputs = build_input_versions()
    df, meta = load_aligned(allow_price_imputation=False)
    df = df.sort_values("trade_date").reset_index(drop=True)
    if build_input_versions() != inputs:
        raise RuntimeError("Backtest inputs changed during loading; retry from a stable snapshot")
    run_manifest = build_run_manifest(df, meta, policy=policy, inputs=inputs)
    print(f"n={len(df)} {df.trade_date.min().date()} → {df.trade_date.max().date()}")

    scenarios = []

    def run_scenario(mode: str, bps: int, label: str, *, tail_mode: str = "strict_evidence", haircut: bool = False) -> None:
        cost = bps / 10000.0
        r = backtest_v2(
            df, meta, buy_cost=cost, sell_cost=cost, mode=mode,
            apply_haircut=haircut, event_exit=True, tail_mode=tail_mode,
        )
        if r["params"].get("tail_mode") != tail_mode:
            raise RuntimeError("Simulator must report the executed tail_mode")
        pack = pack_stats(r)
        scenario = {
            "mode": mode, "cost_label": label, "bps": bps,
            "tail_mode": tail_mode,
            "apply_proxy_adjustment": haircut,
            "scenario_kind": (
                "tail_assumption" if tail_mode == "eod_close_proxy" else
                "proxy_return_stress" if haircut else
                "baseline" if bps == 1 else "cost_stress"
            ),
            "instrument_basis": "industry_index_proxy_not_executable_etf",
            **pack, "params": r["params"],
        }
        scenario["scenario_fingerprint"] = fingerprint({
            "policy_fingerprint": policy["policy_fingerprint"],
            "params": r["params"],
        })
        scenarios.append(scenario)
        print(f"{mode} {label}: {pack['full_sample']['validation_status']}; n={pack['full_sample'].get('trade_count')}")

    for mode in (MODE_CONSERVATIVE, MODE_AGGRESSIVE):
        for bps, label in ((1, "base_1bps"), (15, "stress_15bps"), (30, "stress_30bps")):
            run_scenario(mode, bps, label)
        run_scenario(mode, 1, "proxy_return_stress_1bps", haircut=True)
        run_scenario(mode, 1, "tail_eod_close_proxy_1bps", tail_mode="eod_close_proxy")

    def find(mode: str, label: str) -> dict:
        return next(s for s in scenarios if s["mode"] == mode and s["cost_label"] == label)

    cons = find(MODE_CONSERVATIVE, "base_1bps")
    agg = find(MODE_AGGRESSIVE, "base_1bps")
    execution_complete = all(
        s[period]["performance_valid"]
        for s in scenarios for period in ("full_sample", "oos")
    ) and all(s["walk_forward"]["aggregate"]["performance_valid"] for s in scenarios)

    out = {
        "schema_version": SCHEMA_VERSION,
        "mode": "combined_flex_v2",
        "validation_status": (
            "INCOMPLETE_PROVENANCE" if not policy["complete"] else
            "GENERATED_REPLAY" if execution_complete else "INCOMPLETE_EXECUTION"
        ),
        "validation_kind": "retrospective_replay",
        "independent_parameter_validation": False,
        "run_manifest": run_manifest,
        "generated_at": run_manifest["generated_at"],
        "run_id": run_manifest["run_id"],
        "sample": run_manifest["sample"],
        "policy_fingerprint": policy["policy_fingerprint"],
        "input_fingerprint": inputs["input_fingerprint"],
        "label_cn": "组合 Flex v2（行业指数代理回顾重放，非ETF可复制业绩）",
        "default_mode": MODE_AGGRESSIVE,
        "baseline_tail_mode": "strict_evidence",
        "instrument_basis": "industry_index_proxy_not_executable_etf",
        "metric_labels": {
            "ann_return": "净年化", "max_dd": "净回撤",
            "gross_win_rate": "费用前胜率", "net_win_rate": "净胜率",
            "win_rate": "净胜率（兼容字段）",
        },
        "hold_days_core": CORE_HOLD_DAYS,
        "hold_days_sat": f"{SAT_MIN_HOLD}-{SAT_MAX_HOLD}",
        "satellite_stop_loss": SAT_STOP_LOSS,
        "satellite_take_profit": SAT_TAKE_PROFIT,
        "execution": "默认strict_evidence：无历史尾盘时点证据则T+1开盘；收盘价尾盘代理仅属单独假设场景",
        "backtest_protocol": {
            "price_path": "event-driven account ledger; open executions and daily close marks",
            "right_censoring": "entries follow observed events without inspecting future window completeness; positions still open at sample end retain diagnostic valuation and are excluded from completed trade counts and win rates",
            "core_tail": "strict_evidence is the default; missing historical tail evidence defers to T+1 open",
            "core_tail_quality": "historical EOD confidence is not point-in-time tail evidence",
            "tail_assumption": "eod_close_proxy assumes a T close fill, not a verified executable tail fill",
            "cost": "account fills charge explicit buy/sell costs, including rebalances",
            "proxy": "baseline uses raw industry-index proxies, not tradable ETF performance; return haircuts are separate stress scenarios",
            "observe": "observe-only satellite sleeve uses 0.25 production scale",
            "satellite_risk_exit": (
                f"after {SAT_MIN_HOLD} completed sessions, detect basket return <= {SAT_STOP_LOSS:.0%} "
                f"or >= {SAT_TAKE_PROFIT:.0%} at EOD and execute at next open including the gap"
            ),
            "oos": f"retrospective holdout starts flat on {OOS_SPLIT.date()}; not parameter-independent",
            "walk_forward": "expanding fixed-policy temporal-stability windows; no in-fold tuning",
            "prospective": "BLOCKED_REQUIRES_POINT_IN_TIME_ARCHIVE; backfilled observations are retrospective replay, not strict prospective validation",
            "missing_execution_prices": "incomplete execution suppresses performance; diagnostic counts and issues remain visible",
        },
        "core_only_status": "NOT_RUN_STANDALONE",
        "conservative": {
            "note": "对照口径；总暴露 capped；同一日度路径与成本模型",
            "full_sample": cons["full_sample"],
            "oos": cons["oos"],
            "walk_forward": cons["walk_forward"],
            "prospective": cons["prospective"],
            "params": cons["params"],
            "scenario_fingerprint": cons["scenario_fingerprint"],
            "execution_diagnostics": cons["execution_diagnostics"],
        },
        "aggressive": {
            "note": "生产进取模式；单仓满仓、双仓60/40；卫星-3%止损/+4%止盈；含换仓成本",
            "full_sample": agg["full_sample"],
            "oos": agg["oos"],
            "walk_forward": agg["walk_forward"],
            "prospective": agg["prospective"],
            "params": agg["params"],
            "scenario_fingerprint": agg["scenario_fingerprint"],
            "execution_diagnostics": agg["execution_diagnostics"],
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
            "etf_haircut_note": "费用压力与代理收益折扣独立；行业指数不等于可成交ETF",
        },
        "proxy_return_stress": {
            mode: find(mode, "proxy_return_stress_1bps")
            for mode in (MODE_CONSERVATIVE, MODE_AGGRESSIVE)
        },
        "tail_assumption": {
            mode: find(mode, "tail_eod_close_proxy_1bps")
            for mode in (MODE_CONSERVATIVE, MODE_AGGRESSIVE)
        },
        "caveat_cn": "行业指数代理回放非ETF可复制业绩；默认无尾盘时点证据则T+1；费用前胜率与净胜率分列；回填不构成严格前瞻。",
        "scenarios": scenarios,
    }

    if build_policy_manifest() != policy or build_input_versions() != inputs:
        raise RuntimeError("Policy or input files changed during the run; artifact not published")
    encoded = json.dumps(json_safe(out), ensure_ascii=False, indent=2, allow_nan=False)
    CALCULATED.mkdir(parents=True, exist_ok=True)
    path = CALCULATED / "flex_backtest_stats.json"
    path.write_text(encoded, encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "flex_v2_stats.json").write_text(encoded, encoding="utf-8")
    print("Wrote", path)


if __name__ == "__main__":
    main()
