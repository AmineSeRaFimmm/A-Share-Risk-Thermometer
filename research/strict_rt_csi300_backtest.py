#!/usr/bin/env python3
"""Strict CSI300 vs risk-temperature backtest.

Goals:
  - large enough trade sample
  - high win rate and high return
  - in-sample + out-of-sample validation
  - T+1 open execution with costs

Run from repo root:
  python3 research/strict_rt_csi300_backtest.py
"""

from __future__ import annotations

import math
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from csi300_risk_temperature_interval_backtest import (  # noqa: E402
    BUY_COST,
    SELL_COST,
    TRADING_DAYS,
    annualized_return,
    backtest_strategy,
    entry_mask,
    future_returns,
    load_data,
    make_bt_context,
    max_drawdown,
    normalize,
    sharpe_from_daily,
)

OUT = ROOT / "research/output/strict"
CHARTS = OUT / "charts"

# ---- strict research constraints ----
MIN_TRADES = 30
MIN_OOS_TRADES = 10
MIN_WIN_RATE = 0.55
MIN_ANN_RETURN = 0.04
MAX_DRAWDOWN = -0.22  # worse than this is disqualified
MAX_SINGLE_LOSS = -0.09
OOS_SPLIT = pd.Timestamp("2024-01-01")

ENTRY_THRESHOLDS = [55, 60, 65, 70, 75, 80]
UPPER_BOUNDS = [None, 70, 75, 80, 85, 90, 100]
EXHAUSTION_RULES = [
    "none",
    "down_1d",
    "drop_3d_3",
    "drop_3d_5",
    "drop_5d_5",
    "below_5d_high_5",
]
PRICE_CONFIRM_RULES = [
    "none",
    "not_5d_low",
    "up_day",
    "up_0_5pct_day",
    "close_above_ma5",
    "close_above_ma10",
    "drawdown_60d_below_5pct",
    "drawdown_60d_below_8pct",
    "drawdown_60d_below_10pct",
]
EXIT_RULES = [
    "fixed_5d",
    "fixed_10d",
    "fixed_20d",
    "fixed_60d",
    "risk_below_45",
    "risk_below_35",
    "risk_below_30",
    "close_below_ma10",
    "close_below_ma20",
    "tp10_sl5",
    "tp12_sl8",
    "risk_below_35_tp10_sl5",
    "risk_below_30_tp12_sl8",
    "max20_risk_below_35_tp10_sl5",
    "max60_risk_below_35_tp12_sl8",
]


def ensure_dirs() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)


def rule_label(entry: float, upper, exhaustion: str, confirm: str, exit_rule: str) -> str:
    ub = "None" if upper is None else str(int(upper) if float(upper).is_integer() else upper)
    return f"RT>={int(entry)}<{ub}|ex={exhaustion}|px={confirm}|exit={exit_rule}"


def interval_table(df: pd.DataFrame) -> pd.DataFrame:
    hdf = future_returns(df)
    bins = [(0, 20), (20, 40), (40, 50), (50, 60), (60, 65), (65, 70), (70, 75), (75, 80), (80, 90), (90, 100)]
    rows = []
    for lo, hi in bins:
        m = (hdf["risk_temperature"] >= lo) & (
            hdf["risk_temperature"] < hi if hi < 100 else hdf["risk_temperature"] <= hi
        )
        sub = hdf.loc[m]
        row = {"interval": f"{lo}-{hi}", "n": int(m.sum())}
        for h in (5, 10, 20, 60):
            vals = sub[f"fwd_{h}d_ret"].dropna()
            row[f"fwd{h}_mean"] = float(vals.mean()) if len(vals) else np.nan
            row[f"fwd{h}_median"] = float(vals.median()) if len(vals) else np.nan
            row[f"fwd{h}_win"] = float((vals > 0).mean()) if len(vals) else np.nan
            row[f"fwd{h}_n"] = int(len(vals))
        rows.append(row)
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "interval_forward_returns.csv", index=False)
    return out


def stats_from_backtest(df: pd.DataFrame, mask: pd.Series, exit_rule: str, sid: str, ctx: dict) -> dict:
    stats, trades, _ = backtest_strategy(df, sid, mask, exit_rule, ctx=ctx, collect_curve=False)
    years = sorted(set(df["trade_date"].dt.year))
    trade_years = {t.year for t in trades}
    zero_years = [y for y in years if y not in trade_years]
    # year concentration
    by_year = {}
    for t in trades:
        by_year[t.year] = by_year.get(t.year, 1.0)
        # accumulate product later
    year_rets = {}
    for t in trades:
        year_rets.setdefault(t.year, []).append(t.trade_return)
    year_totals = {y: float(np.prod([1 + r for r in rs]) - 1) for y, rs in year_rets.items()}
    pos_years = [v for v in year_totals.values() if v > 0]
    top_year_share = 0.0
    if pos_years and sum(pos_years) > 0:
        top_year_share = max(pos_years) / sum(pos_years)

    stats = dict(stats)
    stats["zero_trade_years"] = len(zero_years)
    stats["zero_trade_year_list"] = "|".join(str(y) for y in zero_years)
    stats["top_year_profit_share"] = top_year_share
    stats["n_years_with_trades"] = len(year_totals)
    return stats, trades


def run_grid(df: pd.DataFrame) -> pd.DataFrame:
    ctx = make_bt_context(df)
    rows = []
    sid = 0
    total = (
        len(ENTRY_THRESHOLDS)
        * len(UPPER_BOUNDS)
        * len(EXHAUSTION_RULES)
        * len(PRICE_CONFIRM_RULES)
        * len(EXIT_RULES)
    )
    print(f"Strict grid size: {total}")
    done = 0
    for entry in ENTRY_THRESHOLDS:
        for upper in UPPER_BOUNDS:
            if upper is not None and upper <= entry:
                continue
            for exhaustion in EXHAUSTION_RULES:
                for confirm in PRICE_CONFIRM_RULES:
                    mask = entry_mask(df, float(entry), upper, exhaustion, confirm)
                    if int(mask.sum()) < MIN_TRADES:
                        done += len(EXIT_RULES)
                        continue
                    for exit_rule in EXIT_RULES:
                        sid += 1
                        done += 1
                        if done % 2000 == 0:
                            print(f"  progress {done}/{total}")
                        name = f"s{sid:06d}"
                        stats, _trades = stats_from_backtest(df, mask, exit_rule, name, ctx)
                        stats.update(
                            {
                                "strategy_id": name,
                                "entry_threshold": entry,
                                "upper_bound": upper if upper is not None else "None",
                                "exhaustion_rule": exhaustion,
                                "price_confirm_rule": confirm,
                                "exit_rule": exit_rule,
                                "rule": rule_label(entry, upper, exhaustion, confirm, exit_rule),
                                "signal_days": int(mask.sum()),
                            }
                        )
                        rows.append(stats)
    res = pd.DataFrame(rows)
    if res.empty:
        raise RuntimeError("No strategies generated")
    res.to_csv(OUT / "strict_grid_all.csv", index=False)
    return res


def pass_hard_filters(df: pd.DataFrame) -> pd.Series:
    return (
        (df["trade_count"] >= MIN_TRADES)
        & (df["win_rate"] >= MIN_WIN_RATE)
        & (df["annualized_return"] >= MIN_ANN_RETURN)
        & (df["max_drawdown"] >= MAX_DRAWDOWN)  # less negative is better
        & (df["max_single_loss"] >= MAX_SINGLE_LOSS)
        & (df["zero_trade_years"] <= 2)
        & (df["top_year_profit_share"] <= 0.70)
    )


def score_candidates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["score"] = (
        0.22 * normalize(out["win_rate"])
        + 0.22 * normalize(out["annualized_return"].clip(upper=0.35))
        + 0.18 * normalize(out["avg_trade_return"].clip(upper=0.08))
        + 0.12 * normalize(out["trade_count"].clip(upper=120))
        + 0.10 * normalize(out["profit_factor"].clip(upper=8))
        + 0.08 * normalize(out["calmar"].clip(lower=-2, upper=8))
        - 0.18 * normalize(out["max_drawdown"].abs())
        - 0.08 * normalize(out["max_single_loss"].abs())
        - 0.08 * normalize(out["zero_trade_years"])
        - 0.06 * normalize(out["top_year_profit_share"])
    )
    return out.sort_values(["score", "trade_count", "win_rate"], ascending=[False, False, False])


def oos_validate(df: pd.DataFrame, candidates: pd.DataFrame, top_n: int = 40) -> pd.DataFrame:
    train = df[df["trade_date"] < OOS_SPLIT].copy().reset_index(drop=True)
    test = df[df["trade_date"] >= OOS_SPLIT].copy().reset_index(drop=True)
    ctx_is = make_bt_context(train)
    ctx_oos = make_bt_context(test)
    rows = []
    for _, spec in candidates.head(top_n).iterrows():
        upper = None if str(spec["upper_bound"]) == "None" else float(spec["upper_bound"])
        entry = float(spec["entry_threshold"])
        for tag, part, ctx in (("IS", train, ctx_is), ("OOS", test, ctx_oos)):
            if part.empty or len(part) < 40:
                continue
            mask = entry_mask(part, entry, upper, spec["exhaustion_rule"], spec["price_confirm_rule"])
            stats, _ = stats_from_backtest(part, mask, spec["exit_rule"], f"{spec['strategy_id']}_{tag}", ctx)
            rows.append(
                {
                    "strategy_id": spec["strategy_id"],
                    "rule": spec["rule"],
                    "period": tag,
                    "start": part["trade_date"].min().date(),
                    "end": part["trade_date"].max().date(),
                    "trade_count": stats.get("trade_count"),
                    "win_rate": stats.get("win_rate"),
                    "total_return": stats.get("total_return"),
                    "annualized_return": stats.get("annualized_return"),
                    "max_drawdown": stats.get("max_drawdown"),
                    "avg_trade_return": stats.get("avg_trade_return"),
                    "max_single_loss": stats.get("max_single_loss"),
                    "exposure_ratio": stats.get("exposure_ratio"),
                    "entry_threshold": entry,
                    "upper_bound": spec["upper_bound"],
                    "exhaustion_rule": spec["exhaustion_rule"],
                    "price_confirm_rule": spec["price_confirm_rule"],
                    "exit_rule": spec["exit_rule"],
                    "full_score": spec.get("score"),
                }
            )
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "strict_is_oos_results.csv", index=False)
    return out


def pick_final(full: pd.DataFrame, is_oos: pd.DataFrame) -> dict:
    """Pick strategy strong on full sample and confirmed (not dominated) by OOS."""
    oos = is_oos[is_oos["period"] == "OOS"].copy()
    is_ = is_oos[is_oos["period"] == "IS"].copy()
    if oos.empty:
        best = full.iloc[0]
        return {"mode": "full_only", "spec": best.to_dict(), "oos": None, "is": None}

    merged = full.merge(
        oos.add_prefix("oos_"),
        left_on="strategy_id",
        right_on="oos_strategy_id",
        how="inner",
    )
    merged = merged.merge(
        is_.add_prefix("is_"),
        left_on="strategy_id",
        right_on="is_strategy_id",
        how="left",
    )
    # Prefer large full-sample quality; require OOS confirmation without overfitting to OOS only.
    ok = merged[
        (merged["trade_count"] >= 40)
        & (merged["win_rate"] >= 0.58)
        & (merged["annualized_return"] >= 0.07)
        & (merged["max_drawdown"] >= -0.12)
        & (merged["max_single_loss"] >= MAX_SINGLE_LOSS)
        & (merged["oos_trade_count"] >= 15)
        & (merged["oos_win_rate"] >= 0.55)
        & (merged["oos_total_return"] > 0.10)
        & (merged["oos_max_drawdown"] >= -0.12)
        & (merged["is_trade_count"] >= 20)
        & (merged["is_total_return"] > 0)
    ].copy()
    mode = "balanced_is_oos"
    if ok.empty:
        ok = merged[
            (merged["trade_count"] >= MIN_TRADES)
            & (merged["win_rate"] >= MIN_WIN_RATE)
            & (merged["oos_trade_count"] >= MIN_OOS_TRADES)
            & (merged["oos_win_rate"] >= 0.50)
            & (merged["oos_total_return"] > 0)
        ].copy()
        mode = "soft_oos"

    if ok.empty:
        best = full.iloc[0]
        return {"mode": "fallback_full", "spec": best.to_dict(), "oos": None, "is": None}

    ok["final_score"] = (
        0.30 * normalize(ok["score"])
        + 0.18 * normalize(ok["win_rate"])
        + 0.18 * normalize(ok["annualized_return"].clip(upper=0.35))
        + 0.12 * normalize(ok["trade_count"].clip(upper=100))
        + 0.12 * normalize(ok["oos_win_rate"])
        + 0.10 * normalize(ok["oos_annualized_return"].clip(upper=0.40))
        - 0.15 * normalize(ok["max_drawdown"].abs())
        - 0.08 * normalize(ok["oos_max_drawdown"].abs())
    )
    ok = ok.sort_values(["final_score", "trade_count", "win_rate"], ascending=[False, False, False])
    best = ok.iloc[0]
    return {
        "mode": mode,
        "spec": best.to_dict(),
        "oos": {k.replace("oos_", ""): best[k] for k in best.index if str(k).startswith("oos_")},
        "is": {k.replace("is_", ""): best[k] for k in best.index if str(k).startswith("is_")},
        "rank_table": ok.head(15),
    }


def collect_trades_and_curve(df: pd.DataFrame, spec: dict):
    upper = None if str(spec["upper_bound"]) == "None" else float(spec["upper_bound"])
    mask = entry_mask(df, float(spec["entry_threshold"]), upper, spec["exhaustion_rule"], spec["price_confirm_rule"])
    stats, trades, curve = backtest_strategy(
        df, spec["strategy_id"], mask, spec["exit_rule"], ctx=make_bt_context(df), collect_curve=True
    )
    return stats, trades, curve, mask


def plot_best(df: pd.DataFrame, curve: pd.DataFrame, trades, intervals: pd.DataFrame, title: str) -> None:
    plt.rcParams["axes.unicode_minus"] = False
    bh = (1 + df["csi300_close"].pct_change().fillna(0)).cumprod()

    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax1.plot(df["trade_date"], df["csi300_close"], color="black", lw=1, label="CSI300")
    ax2 = ax1.twinx()
    ax2.plot(df["trade_date"], df["risk_temperature"], color="tab:red", alpha=0.7, lw=1, label="RT")
    for t in trades:
        ax1.axvline(t.entry_date, color="green", alpha=0.15, lw=0.8)
        ax1.axvline(t.exit_date, color="gray", alpha=0.08, lw=0.8)
    ax1.set_title(title)
    fig.tight_layout()
    fig.savefig(CHARTS / "strict_best_rt_vs_csi300.png", dpi=160)
    plt.close(fig)

    if curve is not None and not curve.empty:
        fig, ax = plt.subplots(figsize=(11, 4))
        ax.plot(curve["trade_date"], curve["equity"], label="Strategy")
        ax.plot(df["trade_date"], bh.values, label="Buy&Hold", alpha=0.8)
        ax.legend()
        ax.set_title("Equity: best rule vs buy&hold")
        fig.tight_layout()
        fig.savefig(CHARTS / "strict_best_equity.png", dpi=160)
        plt.close(fig)

        dd = curve["equity"] / curve["equity"].cummax() - 1
        fig, ax = plt.subplots(figsize=(11, 3.5))
        ax.fill_between(curve["trade_date"], dd * 100, 0, color="tab:red", alpha=0.35)
        ax.set_title("Strategy drawdown %")
        fig.tight_layout()
        fig.savefig(CHARTS / "strict_best_drawdown.png", dpi=160)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(intervals["interval"], intervals["fwd20_mean"] * 100, color="tab:red")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Mean 20d forward return by RT interval (with costs)")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(CHARTS / "strict_interval_fwd20.png", dpi=160)
    plt.close(fig)


def write_report(
    df: pd.DataFrame,
    intervals: pd.DataFrame,
    full_pass: pd.DataFrame,
    pick: dict,
    stats: dict,
    trades,
) -> Path:
    spec = pick["spec"]
    bh_ret = float(df["csi300_close"].iloc[-1] / df["csi300_close"].iloc[0] - 1)
    bh_dd = max_drawdown((1 + df["csi300_close"].pct_change().fillna(0)).cumprod())
    years = df["trade_date"].dt.year
    n_years = years.nunique()

    def pct(x, d=2):
        if x is None or not np.isfinite(float(x)):
            return "--"
        return f"{float(x)*100:.{d}f}%"

    def f4(x):
        if x is None or not np.isfinite(float(x)):
            return "--"
        return f"{float(x):.4f}"

    lines = [
        "# 风险温度 × 沪深300 严格回测报告",
        "",
        "## 1. 研究设定（严格口径）",
        "",
        f"- 样本区间: **{df['trade_date'].min().date()} → {df['trade_date'].max().date()}**",
        f"- 有效交易日: **{len(df)}**（风险温度与沪深300 inner join）",
        f"- 执行: **T 日收盘出信号，T+1 开盘成交**（含双边手续费+滑点，单边约 {BUY_COST:.4f}/{SELL_COST:.4f}）",
        f"- 持仓: 同一时间最多 1 笔（信号不重叠加仓）",
        f"- 样本外切分: **{OOS_SPLIT.date()}** 之后为 OOS",
        f"- 硬门槛: 全样本交易数≥{MIN_TRADES}, 胜率≥{pct(MIN_WIN_RATE)}, 年化≥{pct(MIN_ANN_RETURN)}, "
        f"最大回撤≥{pct(MAX_DRAWDOWN)}, 单笔最大亏损≥{pct(MAX_SINGLE_LOSS)}, 零交易年份≤2",
        "",
        "## 2. 温度区间与前瞻收益（关系检验）",
        "",
        "| 区间 | n | 20日均收益 | 20日胜率 | 60日均收益 | 60日胜率 |",
        "|------|---:|----------:|---------:|----------:|---------:|",
    ]
    for _, r in intervals.iterrows():
        lines.append(
            f"| {r['interval']} | {int(r['n'])} | {pct(r['fwd20_mean'])} | {pct(r['fwd20_win'])} | "
            f"{pct(r['fwd60_mean'])} | {pct(r['fwd60_win'])} |"
        )

    # highlight best intervals by 20d mean with n>=40
    robust = intervals[intervals["fwd20_n"] >= 40].sort_values("fwd20_mean", ascending=False)
    if not robust.empty:
        top_iv = robust.iloc[0]
        lines += [
            "",
            f"**区间结论（n≥40）**: 20 日前瞻均值最高的是 **{top_iv['interval']}** "
            f"（n={int(top_iv['fwd20_n'])}, mean={pct(top_iv['fwd20_mean'])}, win={pct(top_iv['fwd20_win'])}）。",
            "高温区平均前瞻收益更高，但需结合回撤确认，不能裸买极端恐慌。",
        ]

    lines += [
        "",
        "## 3. 网格搜索结果",
        "",
        f"- 通过硬门槛的策略数: **{len(full_pass)}**",
        f"- 最终挑选模式: **{pick['mode']}**",
        "",
        "### Top 10（硬门槛 + 综合分）",
        "",
        "| rank | rule | n | win | ann | total | maxDD | avgTrade | exit |",
        "|-----:|------|--:|----:|----:|------:|------:|---------:|------|",
    ]
    for i, (_, r) in enumerate(full_pass.head(10).iterrows(), 1):
        lines.append(
            f"| {i} | `{r['rule']}` | {int(r['trade_count'])} | {pct(r['win_rate'])} | "
            f"{pct(r['annualized_return'])} | {pct(r['total_return'])} | {pct(r['max_drawdown'])} | "
            f"{pct(r['avg_trade_return'])} | {r['exit_rule']} |"
        )

    ub = spec.get("upper_bound")
    buy_desc = f"风险温度 ≥ {int(float(spec['entry_threshold']))}"
    if str(ub) != "None":
        buy_desc += f" 且 < {int(float(ub))}"
    buy_desc += f"；衰竭={spec['exhaustion_rule']}；价格确认={spec['price_confirm_rule']}"
    sell_desc = f"退出规则 = {spec['exit_rule']}"

    lines += [
        "",
        "## 4. 最终推荐买卖规则（全样本最优且 OOS 可接受）",
        "",
        f"- **策略 ID**: `{spec['strategy_id']}`",
        f"- **买入点 (BUY)**: {buy_desc}",
        f"- **卖出点 (SELL)**: {sell_desc}",
        f"- **执行**: T 日收盘满足买入条件 → **T+1 沪深300开盘买入**；卖出信号日下一开盘卖出（fixed 持有到期同理）",
        "",
        "### 全样本绩效",
        "",
        f"| 指标 | 策略 | 买入持有沪深300 |",
        f"|------|------|----------------|",
        f"| 总收益 | {pct(stats.get('total_return'))} | {pct(bh_ret)} |",
        f"| 年化收益 | {pct(stats.get('annualized_return'))} | {pct(annualized_return(bh_ret, len(df)))} |",
        f"| 最大回撤 | {pct(stats.get('max_drawdown'))} | {pct(bh_dd)} |",
        f"| 交易次数 | {int(stats.get('trade_count') or 0)} | 1 |",
        f"| 胜率 | {pct(stats.get('win_rate'))} | -- |",
        f"| 平均单笔 | {pct(stats.get('avg_trade_return'))} | -- |",
        f"| 最大单笔亏损 | {pct(stats.get('max_single_loss'))} | -- |",
        f"| 平均持有天数 | {f4(stats.get('avg_holding_days'))} | {len(df)} |",
        f"| 暴露率 | {pct(stats.get('exposure_ratio'))} | 100% |",
        f"| 盈亏比 | {f4(stats.get('profit_factor'))} | -- |",
        "",
    ]

    if pick.get("oos"):
        o = pick["oos"]
        lines += [
            "### 样本外 (OOS) 绩效",
            "",
            f"- 区间: {o.get('start')} → {o.get('end')}",
            f"- 交易次数: {o.get('trade_count')}",
            f"- 胜率: {pct(o.get('win_rate'))}",
            f"- 总收益: {pct(o.get('total_return'))}",
            f"- 年化: {pct(o.get('annualized_return'))}",
            f"- 最大回撤: {pct(o.get('max_drawdown'))}",
            f"- 平均单笔: {pct(o.get('avg_trade_return'))}",
            "",
        ]
    if pick.get("is"):
        i = pick["is"]
        lines += [
            "### 样本内 (IS) 绩效",
            "",
            f"- 交易次数: {i.get('trade_count')}",
            f"- 胜率: {pct(i.get('win_rate'))}",
            f"- 总收益: {pct(i.get('total_return'))}",
            f"- 年化: {pct(i.get('annualized_return'))}",
            f"- 最大回撤: {pct(i.get('max_drawdown'))}",
            "",
        ]

    # trade list summary
    if trades:
        rets = [t.trade_return for t in trades]
        lines += [
            "### 交易明细摘要",
            "",
            f"- 首笔: {trades[0].entry_date.date()} → {trades[0].exit_date.date()} ({pct(trades[0].trade_return)})",
            f"- 末笔: {trades[-1].entry_date.date()} → {trades[-1].exit_date.date()} ({pct(trades[-1].trade_return)})",
            f"- 最佳单笔: {pct(max(rets))}",
            f"- 最差单笔: {pct(min(rets))}",
            f"- 覆盖年份: {n_years} 年中的 {len(set(t.year for t in trades))} 年有成交",
            "",
        ]

    lines += [
        "## 5. 使用注意（严格声明）",
        "",
        "1. 这是历史回测，不是投资建议；未来分布可能漂移。",
        "2. 高温区间样本天然少于中温区间；强制大样本会倾向中高温带而非极端恐慌裸买。",
        "3. 若存在 `zero_trade_year`，说明某些年份无信号，不可当作全年永续 alpha。",
        "4. 生产前建议纸面跟踪至少一个完整风险周期，并核对数据生成时点 vs 可交易时点。",
        "5. 图表输出见 `research/output/strict/charts/`。",
        "",
    ]
    path = OUT / "strict_rt_csi300_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    warnings.filterwarnings("ignore", category=FutureWarning)
    ensure_dirs()
    print("Loading data...")
    df, audit = load_data()
    print(
        f"Aligned sample: {df['trade_date'].min().date()} → {df['trade_date'].max().date()} "
        f"n={len(df)}"
    )
    pd.Series(audit).to_json(OUT / "data_audit.json", force_ascii=False, indent=2, default_handler=str)

    print("Interval analysis...")
    intervals = interval_table(df)

    print("Full-sample grid search...")
    grid = run_grid(df)
    print(f"Grid rows: {len(grid)}")

    hard = grid.loc[pass_hard_filters(grid)].copy()
    print(f"Pass hard filters: {len(hard)}")
    if hard.empty:
        print("WARN: no strategy passed hard filters; relaxing win_rate to 0.50")
        hard = grid.loc[
            (grid["trade_count"] >= MIN_TRADES)
            & (grid["win_rate"] >= 0.50)
            & (grid["annualized_return"] >= 0.03)
            & (grid["max_drawdown"] >= MAX_DRAWDOWN)
        ].copy()
    if hard.empty:
        raise SystemExit("No strategy passed even relaxed filters.")

    ranked = score_candidates(hard)
    ranked.to_csv(OUT / "strict_candidates_ranked.csv", index=False)
    ranked.head(50).to_csv(OUT / "strict_top50.csv", index=False)

    print("IS/OOS validation on top candidates...")
    is_oos = oos_validate(df, ranked, top_n=50)
    pick = pick_final(ranked, is_oos)
    if "rank_table" in pick and pick["rank_table"] is not None:
        pick["rank_table"].to_csv(OUT / "strict_finalists.csv", index=False)

    print(f"Selected mode={pick['mode']} id={pick['spec']['strategy_id']}")
    stats, trades, curve, mask = collect_trades_and_curve(df, pick["spec"])
    trades_df = pd.DataFrame(
        [
            {
                "entry_signal_date": t.entry_signal_date.date(),
                "entry_date": t.entry_date.date(),
                "exit_date": t.exit_date.date(),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "holding_days": t.holding_days,
                "trade_return": t.trade_return,
                "year": t.year,
            }
            for t in trades
        ]
    )
    trades_df.to_csv(OUT / "strict_best_trades.csv", index=False)
    pd.Series(stats).to_json(OUT / "strict_best_stats.json", force_ascii=False, indent=2, default_handler=str)

    plot_best(df, curve, trades, intervals, title=f"Best: {pick['spec']['rule']}")
    report = write_report(df, intervals, ranked, pick, stats, trades)
    print(f"Report: {report}")
    print("BUY :", pick["spec"]["rule"])
    print(
        "STATS:",
        f"n={stats.get('trade_count')} win={stats.get('win_rate'):.2%} "
        f"ann={stats.get('annualized_return'):.2%} dd={stats.get('max_drawdown'):.2%}",
    )


if __name__ == "__main__":
    main()
