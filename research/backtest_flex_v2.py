#!/usr/bin/env python3
"""Flex v2 stress backtest: quality haircut, cost stress, event exit, conservative sizing.

Writes data/calculated/flex_backtest_stats.json for the playbook panel.
"""
from __future__ import annotations

import json
import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.backtest_core_plus_sectors import (  # noqa: E402
    HOLD_DAYS,
    OOS_SPLIT,
    TRADING_DAYS,
    annualized,
    asset_return,
    core_signal,
    detect_stages_row,
    load_aligned,
    max_dd,
)
from src.core.flex_engine import (  # noqa: E402
    CORE_HOLD_DAYS,
    FLEX_SAT_LONG,
    MODE_AGGRESSIVE,
    MODE_CONSERVATIVE,
    QUALITY_RETURN_HAIRCUT,
    QUALITY_WEIGHT,
    SAT_DEFAULT_HOLD,
    SAT_MAX_HOLD,
    SAT_MIN_HOLD,
    STAGE_OPPOSITES,
    STAGE_TIER,
    SIZING,
    merge_satellite_targets,
)
from src.core.sector_etf_map import map_sector  # noqa: E402
from src.storage.paths import CALCULATED  # noqa: E402

OUT = ROOT / "research/output/core_plus_sectors"


def quality_of(name: str) -> str:
    return str(map_sector(name).get("quality") or "missing")


def sleeve_stats(daily: np.ndarray, rets: list[float], label: str, n_days: int) -> dict:
    equity = np.cumprod(1 + daily)
    total = float(equity[-1] - 1) if len(equity) else 0.0
    wins = [r for r in rets if r > 0]
    return {
        "label": label,
        "total_return": total,
        "ann_return": annualized(total, n_days),
        "max_dd": max_dd(equity) if len(equity) else float("nan"),
        "trade_count": len(rets),
        "win_rate": float(np.mean([r > 0 for r in rets])) if rets else float("nan"),
        "avg_trade": float(np.mean(rets)) if rets else float("nan"),
        "exposure_ratio": float(np.mean(daily != 0)),
        "sharpe": float(np.mean(daily) / np.std(daily, ddof=1) * math.sqrt(TRADING_DAYS))
        if np.std(daily, ddof=1) > 0
        else float("nan"),
    }


def period_slice(daily: np.ndarray, dates: pd.Series, rets_with_dates: list[tuple], start=None, end=None) -> dict:
    mask = np.ones(len(daily), dtype=bool)
    if start is not None:
        mask &= dates.values >= np.datetime64(start)
    if end is not None:
        mask &= dates.values < np.datetime64(end)
    d = daily.copy()
    d[~mask] = 0.0
    # approximate trade list filter
    rets = [r for dt, r in rets_with_dates if (start is None or dt >= start) and (end is None or dt < end)]
    n = int(mask.sum())
    return sleeve_stats(d, rets, "period", n)


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
    """mode: conservative | aggressive"""
    n = len(df)
    csi_open = df["csi_open"].to_numpy(dtype=float)
    csi_close = df["csi_close"].to_numpy(dtype=float)
    dates = df["trade_date"]
    sector_open = meta["sector_open"]
    sector_close = meta["sector_close"]
    cfg = SIZING[mode]

    core_daily = np.zeros(n)
    sat_daily = np.zeros(n)
    core_rets: list[float] = []
    sat_rets: list[float] = []
    core_ret_dates: list[tuple] = []
    sat_ret_dates: list[tuple] = []

    def ret_asset(opens, closes, entry_i, exit_i, name: str | None = None) -> float | None:
        # patch costs into asset_return via temporary globals pattern — inline
        if entry_i >= len(opens) or entry_i < 0:
            return None
        px_in = opens[entry_i]
        if not np.isfinite(px_in):
            return None
        if exit_i < len(opens) - 1 and np.isfinite(opens[exit_i]):
            px_out = opens[exit_i]
        else:
            px_out = closes[min(exit_i, len(closes) - 1)]
        if not np.isfinite(px_out):
            return None
        r = (px_out * (1 - sell_cost)) / (px_in * (1 + buy_cost)) - 1
        if apply_haircut and name:
            q = quality_of(name)
            if QUALITY_WEIGHT.get(q, 0) <= 0:
                return None  # weak/missing excluded
            r *= QUALITY_RETURN_HAIRCUT.get(q, 0.85)
        return float(r)

    # --- core ---
    next_free = 0
    for i in range(n - 2):
        if i < next_free:
            continue
        row = df.iloc[i]
        if not core_signal(row):
            continue
        entry_i = i + 1
        exit_i = min(entry_i + CORE_HOLD_DAYS, n - 1)
        r = ret_asset(csi_open, csi_close, entry_i, exit_i, name=None)
        if r is None:
            continue
        # core no haircut
        r = (csi_open[exit_i] * (1 - sell_cost)) / (csi_open[entry_i] * (1 + buy_cost)) - 1 if np.isfinite(csi_open[exit_i]) else r
        hold = max(1, exit_i - entry_i)
        daily_r = (1 + r) ** (1 / hold) - 1
        for j in range(entry_i, exit_i):
            core_daily[j] = daily_r
        core_rets.append(float(r))
        core_ret_dates.append((pd.Timestamp(dates.iloc[entry_i]), float(r)))
        next_free = exit_i + 1

    # --- satellite with multi-stage merge + event exit ---
    i = 0
    while i < n - 2:
        row = df.iloc[i]
        stages = detect_stages_row(row)
        rising = "RISING_HARD" in stages
        longs, _av, _sup = merge_satellite_targets(list(stages), rising_hard=rising)
        high = [s for s in stages if STAGE_TIER.get(s) == "high"]
        obs = [s for s in stages if STAGE_TIER.get(s) == "observe"]
        if not longs or (not high and not obs):
            i += 1
            continue
        if not high and obs:
            longs = longs[:1]
        # filter tradable names present in panels
        use = [x for x in longs if x["name"] in sector_open and QUALITY_WEIGHT.get(quality_of(x["name"]), 0) > 0]
        if not use:
            i += 1
            continue
        primary = next(
            (s for s in ["CSI300_CORE_BUY", "HIGH_COOLING", "ENTER_70_BOUNCE", "RISING_HARD", "FALLING_HARD"] if s in stages),
            stages[0],
        )
        entry_i = i + 1
        # determine exit with event exit
        exit_i = min(entry_i + SAT_DEFAULT_HOLD, n - 1)
        if event_exit:
            for k in range(entry_i + SAT_MIN_HOLD, min(entry_i + SAT_MAX_HOLD, n - 1) + 1):
                if k >= n:
                    break
                st_k = detect_stages_row(df.iloc[min(k, n - 1)])
                # use signal day k-1 features approx at k
                st_sig = detect_stages_row(df.iloc[k - 1]) if k - 1 >= 0 else st_k
                held = k - entry_i
                opposites = STAGE_OPPOSITES.get(primary, set())
                if held >= SAT_MIN_HOLD and opposites.intersection(st_sig):
                    exit_i = k
                    break
                if held >= SAT_MAX_HOLD:
                    exit_i = k
                    break
                if held >= SAT_DEFAULT_HOLD and not any(STAGE_TIER.get(s) in {"high", "observe"} for s in st_sig):
                    exit_i = k
                    break
            else:
                exit_i = min(entry_i + SAT_MAX_HOLD, n - 1)

        rets = []
        weights = []
        for x in use:
            r = ret_asset(sector_open[x["name"]], sector_close[x["name"]], entry_i, exit_i, name=x["name"])
            if r is not None:
                rets.append(r)
                weights.append(max(x.get("weight_in_sat") or 0.0, 1e-6))
        if not rets:
            i += 1
            continue
        w = np.array(weights, dtype=float)
        w = w / w.sum()
        sat_ret = float(np.dot(w, np.array(rets)))
        if not high and obs:
            # observe-only size is applied at portfolio level, not trade ret
            pass
        hold = max(1, exit_i - entry_i)
        daily_r = (1 + sat_ret) ** (1 / hold) - 1
        for j in range(entry_i, exit_i):
            sat_daily[j] = daily_r
        sat_rets.append(sat_ret)
        sat_ret_dates.append((pd.Timestamp(dates.iloc[entry_i]), sat_ret))
        i = exit_i + 1

    # portfolio modes
    port = np.zeros(n)
    observe_scale = 0.25
    for j in range(n):
        c, s = core_daily[j], sat_daily[j]
        c_on, s_on = c != 0, s != 0
        # detect observe-only roughly: not available; use full sat
        w_c = cfg["core_when_signal"] if c_on else 0.0
        w_s = cfg["sat_when_signal"] if s_on else 0.0
        if cfg.get("flex_single_full"):
            if c_on and not s_on:
                w_c, w_s = 1.0, 0.0
            elif s_on and not c_on:
                w_c, w_s = 0.0, 1.0
            elif c_on and s_on:
                w_c, w_s = cfg["core_when_signal"], cfg["sat_when_signal"]
        total = w_c + w_s
        cap = float(cfg["total_cap"])
        if total > cap > 0:
            w_c *= cap / total
            w_s *= cap / total
        port[j] = w_c * c + w_s * s

    stats_core = sleeve_stats(core_daily, core_rets, "core", n)
    stats_sat = sleeve_stats(sat_daily, sat_rets, "satellite", n)
    stats_port = sleeve_stats(port, core_rets + sat_rets, f"flex_{mode}", n)

    oos_start = OOS_SPLIT
    def oos(daily, rdates):
        mask = dates.values >= np.datetime64(oos_start)
        d = daily.copy()
        d[~mask] = 0.0
        rets = [r for dt, r in rdates if dt >= oos_start]
        return sleeve_stats(d, rets, "oos", int(mask.sum()))

    return {
        "core": stats_core,
        "satellite": stats_sat,
        "portfolio": stats_port,
        "oos_portfolio": oos(port, core_ret_dates + sat_ret_dates),
        "oos_core": oos(core_daily, core_ret_dates),
        "params": {
            "buy_cost": buy_cost,
            "sell_cost": sell_cost,
            "mode": mode,
            "apply_haircut": apply_haircut,
            "event_exit": event_exit,
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
        },
        "oos": {
            "total_return": o["total_return"],
            "ann_return": o["ann_return"],
            "max_dd": o["max_dd"],
            "win_rate": o["win_rate"],
            "trade_count": o["trade_count"],
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
    print(f"n={len(df)} {df.trade_date.min().date()} → {df.trade_date.max().date()}")

    scenarios = []
    # Base one-way cost: 1bp (user-verified channel). Stress keeps 15/30 bps.
    for mode in (MODE_CONSERVATIVE, MODE_AGGRESSIVE):
        for bps, label in ((1, "base_1bps"), (15, "stress_15bps"), (30, "stress_30bps")):
            cost = bps / 10000.0
            r = backtest_v2(df, meta, buy_cost=cost, sell_cost=cost, mode=mode, apply_haircut=True, event_exit=True)
            pack = pack_stats(r)
            scenarios.append({"mode": mode, "cost_label": label, "bps": bps, **pack})
            print(
                f"{mode} {label}: ann={pack['full_sample']['ann_return']:.2%} "
                f"dd={pack['full_sample']['max_dd']:.2%} win={pack['full_sample']['win_rate']:.1%} "
                f"n={pack['full_sample']['trade_count']} oos_ann={pack['oos']['ann_return']:.2%}"
            )

    # pick base cases
    def find(mode, label):
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
        "label_cn": "组合 Flex v2（状态机+质量降权+成本压力）",
        "default_mode": MODE_CONSERVATIVE,
        "hold_days_core": CORE_HOLD_DAYS,
        "hold_days_sat": f"{SAT_MIN_HOLD}-{SAT_MAX_HOLD}",
        "execution": "T 收盘信号 → T+1 开盘",
        "core_only": core_only,
        "conservative": {
            "note": "默认推荐；总暴露 capped；质量 haircut；事件退出",
            "full_sample": cons["full_sample"],
            "oos": cons["oos"],
        },
        "aggressive": {
            "note": "单仓满仓 Flex；收益偏高、换手大",
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
            "etf_haircut_note": "proxy×0.85 收益折扣 / weak 剔除；行业指数≠ETF",
        },
        "caveat_cn": "板块用行业指数代理；弱代理不进默认篮子；实盘收益应低于回测。基线单边成本 1bp。",
        "scenarios": [
            {
                "mode": s["mode"],
                "cost_label": s["cost_label"],
                "ann_return": s["full_sample"]["ann_return"],
                "max_dd": s["full_sample"]["max_dd"],
                "win_rate": s["full_sample"]["win_rate"],
                "trade_count": s["full_sample"]["trade_count"],
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
