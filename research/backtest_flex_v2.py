#!/usr/bin/env python3
"""Flex v2 production backtest.

This is the source for data/calculated/flex_backtest_stats.json.
The production contract is:
  - signal at T close, execute at T+1 open
  - daily mark path uses the real open/close path, not endpoint smoothing
  - portfolio costs are charged from target-weight turnover, including rebalances
  - observe-only satellite sleeves use the same 0.25 size scale as production
  - proxy ETF realism discounts gains and amplifies losses
  - OOS is simulated from a fresh OOS start date with no inherited IS position
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
    MODE_AGGRESSIVE,
    MODE_CONSERVATIVE,
    QUALITY_WEIGHT,
    SAT_DEFAULT_HOLD,
    SAT_MAX_HOLD,
    SAT_MIN_HOLD,
    STAGE_OPPOSITES,
    STAGE_TIER,
    SIZING,
    merge_satellite_targets,
    quality_adjusted_return,
)
from src.core.sector_etf_map import map_sector  # noqa: E402
from src.storage.paths import CALCULATED  # noqa: E402

OUT = ROOT / "research/output/core_plus_sectors"
OBSERVE_SCALE = 0.25


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
        exit_i = min(entry_i + CORE_HOLD_DAYS, n - 1)
        path = instrument_path_returns(csi_open, csi_close, entry_i, exit_i)
        if not path:
            continue
        for j, r in path.items():
            core_daily[j] = r
            core_active[j] = True
        core_trades.append(
            Trade("core", entry_i, exit_i, pd.Timestamp(dates.iloc[entry_i]), pd.Timestamp(dates.iloc[exit_i]), _path_total(path))
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
        exit_i = _sat_exit_i(df, entry_i, primary, n, event_exit)

        paths = []
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
                weights.append(max(float(x.get("weight_in_sat") or 0.0), 1e-6))
        if not paths:
            i += 1
            continue
        w = np.asarray(weights, dtype=float)
        w = w / w.sum()
        for j in range(entry_i, exit_i + 1):
            sat_daily[j] = float(sum(w[k] * paths[k].get(j, 0.0) for k in range(len(paths))))
            sat_active[j] = True
            sat_observe[j] = observe_only
        trade_ret = _path_total({j: sat_daily[j] for j in range(entry_i, exit_i + 1)})
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
    """Run full-sample and independent OOS simulations."""
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

    return {
        "core": sleeve_stats(full["core_daily"], full["core_trades"], "core", 0),
        "satellite": sleeve_stats(full["sat_daily"], full["sat_trades"], "satellite", 0),
        "portfolio": sleeve_stats(full["portfolio_daily"], full["trades"], f"flex_{mode}", 0),
        "oos_portfolio": sleeve_stats(oos["portfolio_daily"], oos["trades"], "oos", oos_i),
        "oos_core": sleeve_stats(oos["core_daily"], oos["core_trades"], "oos_core", oos_i),
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
            "oos_protocol": f"fresh simulation from {OOS_SPLIT.date()}",
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
        },
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
        "label_cn": "组合 Flex v2（日度路径+换仓成本+代理亏损惩罚）",
        "default_mode": MODE_AGGRESSIVE,
        "hold_days_core": CORE_HOLD_DAYS,
        "hold_days_sat": f"{SAT_MIN_HOLD}-{SAT_MAX_HOLD}",
        "execution": "T 收盘信号 → T+1 开盘",
        "backtest_protocol": {
            "price_path": "entry open → daily close path → exit open; no endpoint smoothing",
            "cost": "target-weight turnover × one-way bps; entries, exits and rebalances all counted",
            "proxy": "proxy gains are discounted; proxy losses are amplified by the same factor",
            "observe": "observe-only satellite sleeve uses 0.25 production scale",
            "oos": f"independent simulation starts flat on {OOS_SPLIT.date()}",
        },
        "core_only": core_only,
        "conservative": {
            "note": "对照口径；总暴露 capped；同一日度路径与成本模型",
            "full_sample": cons["full_sample"],
            "oos": cons["oos"],
        },
        "aggressive": {
            "note": "生产进取模式；单仓满仓、双仓60/40；含换仓成本",
            "full_sample": agg["full_sample"],
            "oos": agg["oos"],
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
        "caveat_cn": "板块用行业指数代理；弱代理不进默认篮子；回测已计入日度路径、换仓成本和代理亏损惩罚。",
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
