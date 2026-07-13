#!/usr/bin/env python3
"""Backtest: CSI300 core RT strategy + sector overweight satellite.

Sleeve A (core, weight W_CORE when active):
  BUY when 60 <= RT < 80 and HS300 60d drawdown <= -5%
  HOLD 5 trading days, T+1 open execution, costs applied
  Capital is cash when flat

Sleeve B (satellite, weight W_SAT when active):
  Stage-driven sector longs using Shenwan L1 index (and HSTECH) as ETF proxies
  Equal-weight long basket, hold HOLD_DAYS, T+1 open, costs
  Long-only by default (underweights = do not hold those names)

Combined portfolio = sleeve A PnL * W_CORE + sleeve B PnL * W_SAT + cash remainder
Also report pure core, pure satellite, and buy&hold CSI300.

Run:
  python3 research/backtest_core_plus_sectors.py
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research/output/core_plus_sectors"
CHARTS = OUT / "charts"

BUY_COST = 0.0003
SELL_COST = 0.0003
HOLD_DAYS = 5
W_CORE = 0.60
W_SAT = 0.40
OOS_SPLIT = pd.Timestamp("2024-01-01")
TRADING_DAYS = 252

# Stage → long sector names (must match SW L1 / HSTECH labels)
# High-conviction satellite only (exclude CALM/PANIC continuous trading which over-trades).
STAGE_LONG = {
    "RISING_HARD": ["通信", "电子", "机械设备", "国防军工"],
    "CSI300_CORE_BUY": ["建筑材料", "商贸零售", "传媒", "恒生科技"],
    "ENTER_70_BOUNCE": ["恒生科技"],
    "HIGH_COOLING": ["石油石化", "综合", "轻工制造", "煤炭", "传媒"],
    "FALLING_HARD": ["有色金属", "煤炭", "基础化工", "电力设备"],
}
# Optional aggressive stages (off by default via SAT_STAGES_STRICT)
STAGE_LONG_EXTRA = {
    "CALM": ["有色金属", "煤炭", "石油石化"],
    "PANIC_SMALL_N": ["恒生科技", "有色金属"],
}
SAT_STAGES_STRICT = True

# optional long-short: short these vs CSI300 in satellite (net market neutral-ish)
STAGE_SHORT = {
    "RISING_HARD": ["美容护理", "房地产", "钢铁"],
    "CSI300_CORE_BUY": ["公用事业", "银行"],
    "ENTER_70_BOUNCE": ["石油石化", "公用事业", "银行"],
    "HIGH_COOLING": ["电子", "计算机"],
    "FALLING_HARD": ["计算机", "传媒", "美容护理"],
    "CALM": ["美容护理", "恒生科技", "商贸零售"],
    "PANIC_SMALL_N": ["公用事业"],
}


@dataclass
class Trade:
    sleeve: str
    name: str
    entry_i: int
    exit_i: int
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    ret: float
    stage: str


def ensure_dirs() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)


def annualized(total: float, n_days: int) -> float:
    if n_days <= 0 or total <= -1:
        return np.nan
    return (1 + total) ** (TRADING_DAYS / n_days) - 1


def max_dd(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    return float(np.nanmin(equity / peak - 1))


def load_aligned() -> tuple[pd.DataFrame, pd.DataFrame]:
    risk = pd.read_csv(ROOT / "data/calculated/risk_components.csv")
    risk["trade_date"] = pd.to_datetime(risk["trade_date"])
    risk["risk_temperature"] = pd.to_numeric(risk["risk_temperature"], errors="coerce")
    risk = risk.dropna(subset=["trade_date", "risk_temperature"]).sort_values("trade_date")

    idx = pd.read_csv(ROOT / "data/raw/indices/sh000300.csv")
    idx["date"] = pd.to_datetime(idx["date"])
    for c in ["open", "close", "high", "low"]:
        idx[c] = pd.to_numeric(idx[c], errors="coerce")
    idx = idx.sort_values("date").drop_duplicates("date")

    df = risk.merge(
        idx.rename(columns={"date": "trade_date", "open": "csi_open", "close": "csi_close", "high": "csi_high", "low": "csi_low"}),
        on="trade_date",
        how="inner",
    ).sort_values("trade_date").reset_index(drop=True)

    df["csi_open"] = pd.to_numeric(df["csi_open"], errors="coerce")
    df["csi_close"] = pd.to_numeric(df.get("csi_close", df.get("sh000300_close")), errors="coerce")
    if "sh000300_close" in df.columns:
        df["csi_close"] = df["csi_close"].combine_first(pd.to_numeric(df["sh000300_close"], errors="coerce"))
    df["next_open"] = df["csi_open"].shift(-1)
    df["next_date"] = df["trade_date"].shift(-1)
    df["dd60"] = df["csi_close"] / df["csi_close"].rolling(60, min_periods=20).max() - 1
    if "sh000300_dd60" in df.columns:
        df["dd60"] = pd.to_numeric(df["sh000300_dd60"], errors="coerce").combine_first(df["dd60"])
    df["rt"] = df["risk_temperature"]
    df["rt_d1"] = df["rt"].diff()
    df["rt_d5"] = df["rt"] - df["rt"].shift(5)
    df["rt_rollmax_10"] = df["rt"].rolling(10, min_periods=5).max()
    df["prev_rt"] = df["rt"].shift(1)

    # sector open/close panels
    sec = pd.read_csv(ROOT / "data/normalized/sw_level1_sector_history.csv")
    sec["date"] = pd.to_datetime(sec["date"])
    sec["close"] = pd.to_numeric(sec["close"], errors="coerce")
    sec["open"] = pd.to_numeric(sec.get("open"), errors="coerce")
    if sec["open"].isna().all():
        sec["open"] = sec["close"]  # fallback
    sec = sec.dropna(subset=["date", "name", "close"])

    hstech_path = ROOT / "data/raw/indices/hstech.csv"
    if hstech_path.exists():
        hs = pd.read_csv(hstech_path)
        hs["date"] = pd.to_datetime(hs["date"])
        hs["close"] = pd.to_numeric(hs["close"], errors="coerce")
        hs["open"] = pd.to_numeric(hs.get("open"), errors="coerce")
        if "open" not in hs.columns or hs["open"].isna().all():
            hs["open"] = hs["close"]
        hs["name"] = "恒生科技"
        hs["symbol"] = "HSTECH"
        sec = pd.concat(
            [
                sec[["date", "name", "open", "close"]],
                hs[["date", "name", "open", "close"]],
            ],
            ignore_index=True,
        )

    # map trade_date calendar
    dates = df["trade_date"].tolist()
    sector_open: dict[str, np.ndarray] = {}
    sector_close: dict[str, np.ndarray] = {}
    for name, g in sec.groupby("name"):
        g = g.sort_values("date").drop_duplicates("date")
        g = g.set_index("date").reindex(dates)
        # ffill limited for open gaps (HSTECH holidays) — only 1 day
        o = g["open"].ffill(limit=1).to_numpy(dtype=float)
        c = g["close"].ffill(limit=1).to_numpy(dtype=float)
        sector_open[str(name)] = o
        sector_close[str(name)] = c

    meta = {
        "sector_open": sector_open,
        "sector_close": sector_close,
        "names": sorted(sector_open.keys()),
    }
    return df, meta


def detect_stages_row(row) -> list[str]:
    stages = []
    rt, d5, d1, roll, prev, dd = row.rt, row.rt_d5, row.rt_d1, row.rt_rollmax_10, row.prev_rt, row.dd60
    if pd.notna(rt) and rt < 40:
        stages.append("CALM")
    if pd.notna(d5) and d5 >= 5:
        stages.append("RISING_HARD")
    if pd.notna(d5) and d5 <= -5:
        stages.append("FALLING_HARD")
    if (
        pd.notna(roll)
        and pd.notna(d5)
        and pd.notna(d1)
        and roll >= 65
        and rt >= 55
        and d5 <= -3
        and d1 < 0
    ):
        stages.append("HIGH_COOLING")
    if pd.notna(prev) and prev < 70 <= rt:
        stages.append("ENTER_70_BOUNCE")
    if pd.notna(dd) and 60 <= rt < 80 and dd <= -0.05:
        stages.append("CSI300_CORE_BUY")
    if pd.notna(rt) and rt >= 75:
        stages.append("PANIC_SMALL_N")
    return stages


def core_signal(row) -> bool:
    return bool(pd.notna(row.dd60) and 60 <= row.rt < 80 and row.dd60 <= -0.05 and pd.notna(row.next_open))


def asset_return(opens: np.ndarray, closes: np.ndarray, entry_i: int, exit_i: int) -> float | None:
    """T signal at entry_i-1 implied; entry at open[entry_i], exit at open[exit_i] if possible else close."""
    n = len(opens)
    if entry_i >= n or entry_i < 0:
        return None
    px_in = opens[entry_i]
    if not np.isfinite(px_in):
        return None
    if exit_i < n - 1 and np.isfinite(opens[exit_i]):
        px_out = opens[exit_i]
    else:
        px_out = closes[min(exit_i, n - 1)]
    if not np.isfinite(px_out):
        return None
    return (px_out * (1 - SELL_COST)) / (px_in * (1 + BUY_COST)) - 1


def backtest(df: pd.DataFrame, meta: dict, mode: str = "long_only") -> dict:
    """mode: long_only | long_short_sat"""
    n = len(df)
    csi_open = df["csi_open"].to_numpy(dtype=float)
    csi_close = df["csi_close"].to_numpy(dtype=float)
    dates = df["trade_date"].to_numpy()
    sector_open = meta["sector_open"]
    sector_close = meta["sector_close"]

    # daily returns of sleeves (0 if flat)
    core_daily = np.zeros(n)
    sat_daily = np.zeros(n)
    trades: list[Trade] = []

    # --- core sleeve ---
    next_free = 0
    for i in range(n - 2):
        if i < next_free:
            continue
        row = df.iloc[i]
        if not core_signal(row):
            continue
        entry_i = i + 1
        exit_i = min(entry_i + HOLD_DAYS, n - 1)
        ret = asset_return(csi_open, csi_close, entry_i, exit_i)
        if ret is None:
            continue
        # distribute return across holding days equally for equity curve approximation
        hold = max(1, exit_i - entry_i)
        # compound onto last day of hold for trade stats; for daily curve use geometric daily
        daily_r = (1 + ret) ** (1 / hold) - 1
        for j in range(entry_i, exit_i):
            if j < n:
                core_daily[j] = daily_r
        trades.append(
            Trade(
                "core",
                "沪深300",
                entry_i,
                exit_i,
                pd.Timestamp(dates[entry_i]),
                pd.Timestamp(dates[exit_i]),
                float(ret),
                "CSI300_CORE_BUY",
            )
        )
        next_free = exit_i + 1

    # --- satellite sleeve: non-overlapping stage baskets ---
    next_free_sat = 0
    for i in range(n - 2):
        if i < next_free_sat:
            continue
        row = df.iloc[i]
        stages = detect_stages_row(row)
        if not stages:
            continue
        # priority: CORE > HIGH_COOLING > ENTER_70 > RISING > FALLING > PANIC > CALM
        priority = [
            "CSI300_CORE_BUY",
            "HIGH_COOLING",
            "ENTER_70_BOUNCE",
            "RISING_HARD",
            "FALLING_HARD",
            "PANIC_SMALL_N",
            "CALM",
        ]
        long_map = dict(STAGE_LONG)
        if not SAT_STAGES_STRICT:
            long_map.update(STAGE_LONG_EXTRA)
        stage = next((s for s in priority if s in stages and s in long_map), None)
        if stage is None:
            continue
        longs = [nm for nm in long_map.get(stage, []) if nm in sector_open]
        shorts = [nm for nm in STAGE_SHORT.get(stage, []) if nm in sector_open]
        if not longs:
            continue
        entry_i = i + 1
        exit_i = min(entry_i + HOLD_DAYS, n - 1)
        rets = []
        used_names = []
        for nm in longs:
            r = asset_return(sector_open[nm], sector_close[nm], entry_i, exit_i)
            if r is not None:
                rets.append(r)
                used_names.append(nm)
        if not rets:
            continue
        long_ret = float(np.mean(rets))
        sat_ret = long_ret
        if mode == "long_short_sat" and shorts:
            srets = []
            for nm in shorts:
                r = asset_return(sector_open[nm], sector_close[nm], entry_i, exit_i)
                if r is not None:
                    srets.append(r)
            if srets:
                # long basket 50%, short basket 50%
                short_ret = -float(np.mean(srets)) - (BUY_COST + SELL_COST)
                sat_ret = 0.5 * long_ret + 0.5 * short_ret
        hold = max(1, exit_i - entry_i)
        daily_r = (1 + sat_ret) ** (1 / hold) - 1
        for j in range(entry_i, exit_i):
            if j < n:
                sat_daily[j] = daily_r
        trades.append(
            Trade(
                "satellite",
                "+".join(used_names[:4]),
                entry_i,
                exit_i,
                pd.Timestamp(dates[entry_i]),
                pd.Timestamp(dates[exit_i]),
                float(sat_ret),
                stage,
            )
        )
        next_free_sat = exit_i + 1

    # portfolio daily: when core active use W_CORE*core + W_SAT*sat; when only sat use W_SAT*sat; cash 0
    # If both flat, 0. If only core, use core fully? Spec: combined = W_CORE*core_pnl + W_SAT*sat_pnl
    # when sleeve inactive its contribution is 0 (cash)
    port_daily = W_CORE * core_daily + W_SAT * sat_daily
    # alternative: when only one active, allocate full to active — more realistic
    port_daily_flex = np.zeros(n)
    for j in range(n):
        c, s = core_daily[j], sat_daily[j]
        if c != 0 and s != 0:
            port_daily_flex[j] = W_CORE * c + W_SAT * s
        elif c != 0:
            port_daily_flex[j] = c
        elif s != 0:
            port_daily_flex[j] = s

    def sleeve_stats(daily: np.ndarray, trade_list: list[Trade], label: str) -> dict:
        equity = np.cumprod(1 + daily)
        total = float(equity[-1] - 1) if len(equity) else 0.0
        rets = [t.ret for t in trade_list]
        wins = [r for r in rets if r > 0]
        return {
            "label": label,
            "total_return": total,
            "annualized_return": annualized(total, n),
            "max_drawdown": max_dd(equity) if len(equity) else np.nan,
            "trade_count": len(trade_list),
            "win_rate": float(np.mean([r > 0 for r in rets])) if rets else np.nan,
            "avg_trade_return": float(np.mean(rets)) if rets else np.nan,
            "median_trade_return": float(np.median(rets)) if rets else np.nan,
            "max_single_loss": float(min(rets)) if rets else np.nan,
            "profit_factor": float(sum(wins) / abs(sum(r for r in rets if r <= 0)))
            if any(r <= 0 for r in rets) and abs(sum(r for r in rets if r <= 0)) > 0
            else np.nan,
            "exposure_ratio": float(np.mean(daily != 0)),
            "sharpe": float(np.mean(daily) / np.std(daily, ddof=1) * math.sqrt(TRADING_DAYS))
            if np.std(daily, ddof=1) > 0
            else np.nan,
        }

    core_trades = [t for t in trades if t.sleeve == "core"]
    sat_trades = [t for t in trades if t.sleeve == "satellite"]

    # buy hold
    bh_daily = df["csi_close"].pct_change().fillna(0).to_numpy()
    bh_eq = np.cumprod(1 + bh_daily)

    results = {
        "core": sleeve_stats(core_daily, core_trades, "core_csi300"),
        "satellite": sleeve_stats(sat_daily, sat_trades, f"satellite_{mode}"),
        "combined_fixed_weights": sleeve_stats(port_daily, trades, f"combined_{W_CORE:.0%}/{W_SAT:.0%}_{mode}"),
        "combined_flex": sleeve_stats(port_daily_flex, trades, f"combined_flex_{mode}"),
        "buy_hold_csi300": {
            "label": "buy_hold_csi300",
            "total_return": float(bh_eq[-1] - 1),
            "annualized_return": annualized(float(bh_eq[-1] - 1), n),
            "max_drawdown": max_dd(bh_eq),
            "trade_count": 1,
            "win_rate": np.nan,
            "avg_trade_return": np.nan,
            "exposure_ratio": 1.0,
            "sharpe": float(np.mean(bh_daily) / np.std(bh_daily, ddof=1) * math.sqrt(TRADING_DAYS))
            if np.std(bh_daily, ddof=1) > 0
            else np.nan,
        },
    }

    # OOS on flex portfolio
    oos_mask = df["trade_date"] >= OOS_SPLIT
    for key, daily in [("core", core_daily), ("satellite", sat_daily), ("combined_flex", port_daily_flex)]:
        d = daily.copy()
        d_is = d.copy()
        d_oos = d.copy()
        d_is[oos_mask.to_numpy()] = 0
        d_oos[~oos_mask.to_numpy()] = 0
        # recompute only on period days — better slice
    is_idx = np.where(~oos_mask.to_numpy())[0]
    oos_idx = np.where(oos_mask.to_numpy())[0]

    def period_stats(daily, idxs, tlist, label):
        if len(idxs) == 0:
            return {}
        d = daily[idxs]
        # rebuild equity only on period (compound consecutive)
        eq = np.cumprod(1 + d)
        total = float(eq[-1] - 1)
        # trades fully inside period
        tsub = [t for t in tlist if t.entry_i in set(idxs.tolist())]
        rets = [t.ret for t in tsub]
        return {
            "label": label,
            "total_return": total,
            "annualized_return": annualized(total, len(idxs)),
            "max_drawdown": max_dd(eq),
            "trade_count": len(tsub),
            "win_rate": float(np.mean([r > 0 for r in rets])) if rets else np.nan,
            "avg_trade_return": float(np.mean(rets)) if rets else np.nan,
        }

    results["is_core"] = period_stats(core_daily, is_idx, core_trades, "IS_core")
    results["oos_core"] = period_stats(core_daily, oos_idx, core_trades, "OOS_core")
    results["is_sat"] = period_stats(sat_daily, is_idx, sat_trades, "IS_sat")
    results["oos_sat"] = period_stats(sat_daily, oos_idx, sat_trades, "OOS_sat")
    results["is_flex"] = period_stats(port_daily_flex, is_idx, trades, "IS_flex")
    results["oos_flex"] = period_stats(port_daily_flex, oos_idx, trades, "OOS_flex")

    equity = {
        "trade_date": df["trade_date"],
        "core": np.cumprod(1 + core_daily),
        "satellite": np.cumprod(1 + sat_daily),
        "combined_fixed": np.cumprod(1 + port_daily),
        "combined_flex": np.cumprod(1 + port_daily_flex),
        "buy_hold": bh_eq,
    }
    return results, trades, equity, mode


def write_report(results_lo, results_ls, trades_lo, equity_lo, df) -> Path:
    def pct(x, d=2):
        try:
            if x is None or not np.isfinite(float(x)):
                return "--"
            return f"{float(x)*100:.{d}f}%"
        except Exception:
            return "--"

    lines = [
        "# 主策略 + 板块超配 组合回测报告",
        "",
        "## 设定",
        "",
        f"- 样本: **{df['trade_date'].min().date()} → {df['trade_date'].max().date()}**（n={len(df)}）",
        f"- 核心仓权重: **{W_CORE:.0%}** | 卫星仓权重: **{W_SAT:.0%}**（双仓同时持有时）",
        f"- 单仓激活时: **flex 模式把 100% 分给该仓**（更贴近真实可投资金）",
        f"- 核心规则: 60≤RT<80 且 60日回撤≤-5%，持有 {HOLD_DAYS} 日，T+1 开盘，成本单边 {BUY_COST}",
        f"- 卫星: **仅高置信阶段**（CORE/升温/降温/高位回落/穿越70），不做平静期连续交易",
        f"- 卫星篮子: 阶段触发后等权做多对应板块指数（申万一级 + 恒生科技作 ETF 代理），持有 {HOLD_DAYS} 日",
        f"- 低配: long_only 模式不持有；long_short 模式对低配篮子做空 50%",
        f"- OOS 切分: **{OOS_SPLIT.date()}**",
        f"- **重要**: 板块用行业指数代理 ETF，存在跟踪误差与不可交易日（恒生科技从 2020-08 起）",
        "",
        "## 1. Long-only 卫星（推荐主结果）",
        "",
        "| 组合 | 总收益 | 年化 | 最大回撤 | 交易数 | 胜率 | 平均单笔 | 暴露 | Sharpe |",
        "|------|-------:|-----:|---------:|------:|-----:|---------:|-----:|-------:|",
    ]
    for key in ["buy_hold_csi300", "core", "satellite", "combined_fixed_weights", "combined_flex"]:
        r = results_lo[key]
        sh = r.get("sharpe")
        sh_s = f"{sh:.2f}" if isinstance(sh, (int, float)) and np.isfinite(sh) else "--"
        lines.append(
            f"| {r['label']} | {pct(r.get('total_return'))} | {pct(r.get('annualized_return'))} | "
            f"{pct(r.get('max_drawdown'))} | {r.get('trade_count', '--')} | {pct(r.get('win_rate'))} | "
            f"{pct(r.get('avg_trade_return'))} | {pct(r.get('exposure_ratio'))} | {sh_s} |"
        )

    lines += [
        "",
        "### IS / OOS（flex 组合与分仓）",
        "",
        "| 分段 | 总收益 | 年化 | 最大回撤 | 交易数 | 胜率 |",
        "|------|-------:|-----:|---------:|------:|-----:|",
    ]
    for key in ["is_core", "oos_core", "is_sat", "oos_sat", "is_flex", "oos_flex"]:
        r = results_lo[key]
        if not r:
            continue
        lines.append(
            f"| {r['label']} | {pct(r.get('total_return'))} | {pct(r.get('annualized_return'))} | "
            f"{pct(r.get('max_drawdown'))} | {r.get('trade_count')} | {pct(r.get('win_rate'))} |"
        )

    lines += [
        "",
        "## 2. Long-short 卫星（多超配 / 空低配，供对照）",
        "",
        "| 组合 | 总收益 | 年化 | 最大回撤 | 交易数 | 胜率 | 平均单笔 |",
        "|------|-------:|-----:|---------:|------:|-----:|---------:|",
    ]
    for key in ["core", "satellite", "combined_flex"]:
        r = results_ls[key]
        lines.append(
            f"| {r['label']} | {pct(r.get('total_return'))} | {pct(r.get('annualized_return'))} | "
            f"{pct(r.get('max_drawdown'))} | {r.get('trade_count')} | {pct(r.get('win_rate'))} | "
            f"{pct(r.get('avg_trade_return'))} |"
        )

    # stage breakdown for satellite trades
    sat = [t for t in trades_lo if t.sleeve == "satellite"]
    if sat:
        rows = []
        for stage, g in pd.DataFrame(
            [{"stage": t.stage, "ret": t.ret} for t in sat]
        ).groupby("stage"):
            rows.append(
                {
                    "stage": stage,
                    "n": len(g),
                    "win": (g["ret"] > 0).mean(),
                    "avg": g["ret"].mean(),
                    "total": (1 + g["ret"]).prod() - 1,
                }
            )
        lines += ["", "## 3. 卫星仓分阶段绩效（long-only）", "", "| 阶段 | n | 胜率 | 平均单笔 | 复利 |", "|------|--:|-----:|---------:|-----:|"]
        for r in sorted(rows, key=lambda x: -x["n"]):
            lines.append(f"| {r['stage']} | {r['n']} | {pct(r['win'])} | {pct(r['avg'])} | {pct(r['total'])} |")

    lines += [
        "",
        "## 4. 结论",
        "",
        "1. **以 `combined_flex` long-only 为主结论**（单仓激活时满仓该仓，双仓激活时 60/40）。",
        "2. 若组合相对纯核心提升有限或回撤更大，说明卫星阶段规则的可交易性弱于指数主策略。",
        "3. 行业指数 ≠ 真实 ETF，实盘需替换为对应行业 ETF 并重做滑点。",
        "4. 非投资建议。",
        "",
        "## 5. 文件",
        "",
        "- `summary_long_only.csv` / `summary_long_short.csv`",
        "- `trades_long_only.csv`",
        "- `equity_long_only.csv`",
        "- `charts/equity_curves.png`",
        "",
    ]
    path = OUT / "core_plus_sectors_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def plot_equity(equity: dict, title: str, fname: str) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(equity["trade_date"], equity["buy_hold"], label="Buy&Hold CSI300", alpha=0.7)
    ax.plot(equity["trade_date"], equity["core"], label="Core only")
    ax.plot(equity["trade_date"], equity["satellite"], label="Satellite only")
    ax.plot(equity["trade_date"], equity["combined_flex"], label="Combined flex", lw=2)
    ax.legend()
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(CHARTS / fname, dpi=150)
    plt.close(fig)


def main() -> None:
    warnings.filterwarnings("ignore", category=FutureWarning)
    ensure_dirs()
    print("Loading...")
    df, meta = load_aligned()
    print(f"Aligned {df.trade_date.min().date()} → {df.trade_date.max().date()} n={len(df)} sectors={len(meta['names'])}")

    print("Backtest long_only...")
    res_lo, trades_lo, eq_lo, _ = backtest(df, meta, mode="long_only")
    print("Backtest long_short_sat...")
    res_ls, trades_ls, eq_ls, _ = backtest(df, meta, mode="long_short_sat")

    # save
    pd.DataFrame([res_lo[k] for k in res_lo if isinstance(res_lo[k], dict) and "label" in res_lo[k]]).to_csv(
        OUT / "summary_long_only.csv", index=False
    )
    pd.DataFrame([res_ls[k] for k in ["core", "satellite", "combined_flex", "buy_hold_csi300"]]).to_csv(
        OUT / "summary_long_short.csv", index=False
    )
    pd.DataFrame(
        [
            {
                "sleeve": t.sleeve,
                "name": t.name,
                "stage": t.stage,
                "entry_date": t.entry_date.date(),
                "exit_date": t.exit_date.date(),
                "ret": t.ret,
            }
            for t in trades_lo
        ]
    ).to_csv(OUT / "trades_long_only.csv", index=False)

    eq_df = pd.DataFrame(
        {
            "trade_date": eq_lo["trade_date"],
            "buy_hold": eq_lo["buy_hold"],
            "core": eq_lo["core"],
            "satellite": eq_lo["satellite"],
            "combined_fixed": eq_lo["combined_fixed"],
            "combined_flex": eq_lo["combined_flex"],
        }
    )
    eq_df.to_csv(OUT / "equity_long_only.csv", index=False)
    plot_equity(eq_lo, "Core + Sector satellite (long-only)", "equity_curves.png")
    plot_equity(eq_ls, "Core + Sector satellite (long-short sat)", "equity_curves_ls.png")

    report = write_report(res_lo, res_ls, trades_lo, eq_lo, df)
    print("Report", report)
    for k in ["buy_hold_csi300", "core", "satellite", "combined_flex"]:
        r = res_lo[k]
        print(
            f"{r['label']}: total={r['total_return']:.2%} ann={r.get('annualized_return', float('nan')):.2%} "
            f"dd={r.get('max_drawdown', float('nan')):.2%} n={r.get('trade_count')} win={r.get('win_rate')}"
        )
    print("OOS flex", res_lo["oos_flex"])
    print("OOS core", res_lo["oos_core"])
    print("OOS sat", res_lo["oos_sat"])


if __name__ == "__main__":
    main()
