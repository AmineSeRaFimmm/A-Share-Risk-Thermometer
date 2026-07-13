#!/usr/bin/env python3
"""Threshold-triggered CSI300 pyramid buying research.

Rules:
- T day close reads risk_temperature.
- T+1 CSI300 open executes buy/add/sell.
- When flat and risk_temperature >= threshold, buy 10000 CNY.
- While holding, every additional 3% drop from the last buy trigger adds 2000 CNY.
- If price rises, do not add.
- When whole position reaches take-profit target, sell all at T+1 open.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(".")
RISK_PATH = ROOT / "data/calculated/risk_components.csv"
INDEX_PATH = ROOT / "data/raw/indices/sh000300.csv"
OUT = ROOT / "research/output"

INITIAL_BUY = 10_000.0
ADD_BUY = 2_000.0
DROP_STEP = 0.03
THRESHOLDS = [65, 70, 75, 80, 85, 90, 95]
TAKE_PROFITS = [0.10, 0.125, 0.15]
BUY_FEE = 0.0001
SELL_FEE = 0.0001
SLIPPAGE = 0.0002
BUY_COST = BUY_FEE + SLIPPAGE
SELL_COST = SELL_FEE + SLIPPAGE
TRADING_DAYS = 252


@dataclass
class Trade:
    threshold: int
    take_profit: float
    entry_signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    exit_signal_date: pd.Timestamp | None
    exit_date: pd.Timestamp | None
    holding_days: int
    add_count: int
    buy_count: int
    invested_amount: float
    avg_cost: float
    exit_value: float
    profit_amount: float
    return_rate: float
    max_drawdown_on_position: float
    max_capital_at_risk: float
    status: str


def load_data() -> pd.DataFrame:
    risk = pd.read_csv(RISK_PATH)
    idx = pd.read_csv(INDEX_PATH)
    risk["trade_date"] = pd.to_datetime(risk["trade_date"])
    idx["date"] = pd.to_datetime(idx["date"])
    risk["risk_temperature"] = pd.to_numeric(risk["risk_temperature"], errors="coerce")
    for col in ["open", "close", "high", "low"]:
        idx[col] = pd.to_numeric(idx[col], errors="coerce")
    idx = idx.rename(
        columns={
            "date": "trade_date",
            "open": "csi300_open",
            "close": "csi300_close",
            "high": "csi300_high",
            "low": "csi300_low",
        }
    )
    df = pd.merge(
        risk[["trade_date", "risk_temperature", "quality", "model_confidence"]],
        idx[["trade_date", "csi300_open", "csi300_close", "csi300_high", "csi300_low"]],
        on="trade_date",
        how="inner",
    )
    df = df.sort_values("trade_date").drop_duplicates("trade_date").reset_index(drop=True)
    df["next_open"] = df["csi300_open"].shift(-1)
    df["next_date"] = df["trade_date"].shift(-1)
    return df


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return np.nan
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def annualized_return(total_return: float, days: int) -> float:
    if days <= 0 or total_return <= -1:
        return np.nan
    return (1 + total_return) ** (TRADING_DAYS / days) - 1


def sharpe(returns: pd.Series) -> float:
    vals = returns.replace([np.inf, -np.inf], np.nan).dropna()
    if len(vals) < 3 or vals.std(ddof=1) == 0:
        return np.nan
    return float(vals.mean() / vals.std(ddof=1) * np.sqrt(TRADING_DAYS))


def run_strategy(df: pd.DataFrame, threshold: int, take_profit: float) -> tuple[list[Trade], pd.DataFrame]:
    dates = df["trade_date"].to_numpy()
    opens = df["csi300_open"].to_numpy(float)
    closes = df["csi300_close"].to_numpy(float)
    lows = df["csi300_low"].to_numpy(float)
    risk = df["risk_temperature"].to_numpy(float)
    next_open = df["next_open"].to_numpy(float)

    trades: list[Trade] = []
    cash_profit = 0.0
    equity = []
    in_pos = False
    shares = 0.0
    invested = 0.0
    avg_cost = np.nan
    last_buy_trigger_price = np.nan
    entry_signal_i = entry_i = None
    max_capital = 0.0
    min_unrealized = 0.0
    add_count = 0
    buy_count = 0

    for i in range(len(df) - 1):
        mark_value = shares * closes[i] if in_pos else 0.0
        total_equity = cash_profit + mark_value - invested
        equity.append({"trade_date": dates[i], "equity": total_equity})

        if not in_pos:
            if risk[i] >= threshold and np.isfinite(next_open[i]):
                px = next_open[i] * (1 + BUY_COST)
                shares = INITIAL_BUY / px
                invested = INITIAL_BUY
                avg_cost = px
                last_buy_trigger_price = px
                entry_signal_i = i
                entry_i = i + 1
                max_capital = invested
                min_unrealized = 0.0
                add_count = 0
                buy_count = 1
                in_pos = True
            continue

        unrealized_close_ret = (shares * closes[i] - invested) / invested if invested > 0 else np.nan
        if np.isfinite(unrealized_close_ret):
            min_unrealized = min(min_unrealized, unrealized_close_ret)

        if unrealized_close_ret >= take_profit and np.isfinite(next_open[i]):
            exit_i = i + 1
            exit_value = shares * next_open[i] * (1 - SELL_COST)
            profit = exit_value - invested
            cash_profit += profit
            trades.append(
                Trade(
                    threshold=threshold,
                    take_profit=take_profit,
                    entry_signal_date=pd.Timestamp(dates[entry_signal_i]),
                    entry_date=pd.Timestamp(dates[entry_i]),
                    exit_signal_date=pd.Timestamp(dates[i]),
                    exit_date=pd.Timestamp(dates[exit_i]),
                    holding_days=int(exit_i - entry_i),
                    add_count=add_count,
                    buy_count=buy_count,
                    invested_amount=float(invested),
                    avg_cost=float(avg_cost),
                    exit_value=float(exit_value),
                    profit_amount=float(profit),
                    return_rate=float(profit / invested),
                    max_drawdown_on_position=float(min_unrealized),
                    max_capital_at_risk=float(max_capital),
                    status="closed",
                )
            )
            in_pos = False
            shares = 0.0
            invested = 0.0
            avg_cost = np.nan
            last_buy_trigger_price = np.nan
            continue

        if closes[i] <= last_buy_trigger_price * (1 - DROP_STEP) and np.isfinite(next_open[i]):
            add_px = next_open[i] * (1 + BUY_COST)
            add_shares = ADD_BUY / add_px
            shares += add_shares
            invested += ADD_BUY
            avg_cost = invested / shares
            last_buy_trigger_price = add_px
            add_count += 1
            buy_count += 1
            max_capital = max(max_capital, invested)

    if in_pos:
        last_i = len(df) - 1
        exit_value = shares * closes[last_i] * (1 - SELL_COST)
        profit = exit_value - invested
        trades.append(
            Trade(
                threshold=threshold,
                take_profit=take_profit,
                entry_signal_date=pd.Timestamp(dates[entry_signal_i]),
                entry_date=pd.Timestamp(dates[entry_i]),
                exit_signal_date=None,
                exit_date=None,
                holding_days=int(last_i - entry_i),
                add_count=add_count,
                buy_count=buy_count,
                invested_amount=float(invested),
                avg_cost=float(avg_cost),
                exit_value=float(exit_value),
                profit_amount=float(profit),
                return_rate=float(profit / invested),
                max_drawdown_on_position=float(min_unrealized),
                max_capital_at_risk=float(max_capital),
                status="open",
            )
        )

    equity_df = pd.DataFrame(equity)
    if equity_df.empty:
        equity_df = pd.DataFrame({"trade_date": df["trade_date"], "equity": 0.0})
    equity_df["daily_change"] = equity_df["equity"].diff().fillna(0)
    return trades, equity_df


def summarize(trades: list[Trade], equity: pd.DataFrame, threshold: int, take_profit: float, sample_days: int) -> dict:
    closed = [t for t in trades if t.status == "closed"]
    all_returns = [t.return_rate for t in closed]
    profits = [t.profit_amount for t in closed]
    open_trades = [t for t in trades if t.status == "open"]
    total_invested_all = sum(t.invested_amount for t in trades)
    total_invested_closed = sum(t.invested_amount for t in closed)
    total_profit_closed = sum(profits)
    open_unrealized = sum(t.profit_amount for t in open_trades)
    total_profit_including_open = total_profit_closed + open_unrealized
    max_capital_per_trade = max([t.max_capital_at_risk for t in trades], default=0.0)
    total_holding_days = sum(t.holding_days for t in trades)
    mdd_equity = max_drawdown(equity["equity"] - equity["equity"].min() + 1) if not equity.empty else np.nan
    return {
        "threshold": threshold,
        "take_profit_target": take_profit,
        "trade_count_total": len(trades),
        "trade_count_closed": len(closed),
        "open_trade_count": len(open_trades),
        "total_buy_amount_all_trades": total_invested_all,
        "total_buy_amount_closed_trades": total_invested_closed,
        "max_capital_single_trade": max_capital_per_trade,
        "avg_buy_amount_per_trade": np.mean([t.invested_amount for t in trades]) if trades else np.nan,
        "avg_add_count": np.mean([t.add_count for t in trades]) if trades else np.nan,
        "max_add_count": max([t.add_count for t in trades], default=0),
        "avg_holding_days_closed": np.mean([t.holding_days for t in closed]) if closed else np.nan,
        "median_holding_days_closed": np.median([t.holding_days for t in closed]) if closed else np.nan,
        "max_holding_days": max([t.holding_days for t in trades], default=0),
        "total_holding_days": total_holding_days,
        "exposure_ratio": total_holding_days / sample_days,
        "win_rate_closed": np.mean([r > 0 for r in all_returns]) if all_returns else np.nan,
        "total_profit_closed": total_profit_closed,
        "total_profit_including_open": total_profit_including_open,
        "avg_profit_per_closed_trade": np.mean(profits) if profits else np.nan,
        "median_profit_per_closed_trade": np.median(profits) if profits else np.nan,
        "avg_return_closed": np.mean(all_returns) if all_returns else np.nan,
        "median_return_closed": np.median(all_returns) if all_returns else np.nan,
        "best_trade_return": max(all_returns, default=np.nan),
        "worst_trade_return": min(all_returns, default=np.nan),
        "worst_position_drawdown": min([t.max_drawdown_on_position for t in trades], default=np.nan),
        "profit_factor": sum(p for p in profits if p > 0) / abs(sum(p for p in profits if p < 0)) if any(p < 0 for p in profits) else (99.0 if profits else np.nan),
        "equity_max_drawdown_proxy": mdd_equity,
        "annualized_profit_on_max_capital": annualized_return(total_profit_including_open / max_capital_per_trade, sample_days) if max_capital_per_trade > 0 else np.nan,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = load_data()
    summary_rows = []
    trade_rows = []
    for threshold in THRESHOLDS:
        for take_profit in TAKE_PROFITS:
            trades, equity = run_strategy(df, threshold, take_profit)
            summary_rows.append(summarize(trades, equity, threshold, take_profit, len(df)))
            for idx, trade in enumerate(trades, start=1):
                trade_rows.append({"threshold": threshold, "take_profit_target": take_profit, "trade_no": idx, **trade.__dict__})

    summary = pd.DataFrame(summary_rows)
    trades_df = pd.DataFrame(trade_rows)
    summary.to_csv(OUT / "csi300_threshold_pyramid_summary.csv", index=False)
    trades_df.to_csv(OUT / "csi300_threshold_pyramid_trades.csv", index=False)

    filtered = summary[(summary["trade_count_closed"] >= 3) & (summary["open_trade_count"] == 0)].copy()
    if filtered.empty:
        filtered = summary[summary["trade_count_closed"] >= 3].copy()
    best = filtered.sort_values(
        ["annualized_profit_on_max_capital", "worst_position_drawdown", "trade_count_closed"],
        ascending=[False, False, False],
    ).iloc[0] if not filtered.empty else summary.iloc[0]

    lines = [
        "# 沪深300阈值金字塔加仓策略验证",
        "",
        "## 回测口径",
        "",
        f"- 样本区间: {df['trade_date'].min().date()} 至 {df['trade_date'].max().date()}",
        f"- 有效样本数: {len(df)}",
        "- 信号: T日收盘 risk_temperature 达到阈值。",
        "- 执行: T+1 沪深300 open 买入/加仓/卖出。",
        f"- 初始买入: {INITIAL_BUY:.0f} 元。",
        f"- 加仓: 每从上一次买入触发价继续下跌 {DROP_STEP:.0%}，加 {ADD_BUY:.0f} 元；上涨不加。",
        "- 止盈: 整体持仓收益达到 10% / 12.5% / 15% 分别测试，T+1 open 全部卖出。",
        "- 成本: 买入万一+0.02%滑点；卖出万一+0.02%滑点。",
        "",
        "## 最优候选",
        "",
        f"- 阈值: {int(best['threshold'])}",
        f"- 止盈目标: {best['take_profit_target']:.1%}",
        f"- 总交易次数: {int(best['trade_count_total'])}",
        f"- 已完成交易: {int(best['trade_count_closed'])}",
        f"- 未平仓交易: {int(best['open_trade_count'])}",
        f"- 全部买入金额: {best['total_buy_amount_all_trades']:.0f} 元",
        f"- 单轮最大占用资金: {best['max_capital_single_trade']:.0f} 元",
        f"- 已完成盈利金额: {best['total_profit_closed']:.2f} 元",
        f"- 含未平仓盈亏: {best['total_profit_including_open']:.2f} 元",
        f"- 已完成平均收益率: {best['avg_return_closed']:.2%}",
        f"- 已完成胜率: {best['win_rate_closed']:.2%}",
        f"- 平均持仓天数: {best['avg_holding_days_closed']:.1f}",
        f"- 最大持仓内回撤: {best['worst_position_drawdown']:.2%}",
        "",
        "## 重要结论",
        "",
        "- 阈值越高，交易次数越少，容易出现样本不足和单次行情主导结果。",
        "- 该策略的核心风险不是胜率，而是连续下跌时资金占用和持仓内回撤。",
        "- 如果要实盘化，应限制最大加仓次数或最大单轮资金，否则极端下跌会持续摊低但风险暴露扩大。",
        "",
        "## 全部参数汇总",
        "",
        summary.to_markdown(index=False),
        "",
    ]
    (OUT / "csi300_threshold_pyramid_report.md").write_text("\n".join(lines), encoding="utf-8")

    print("有效回测区间:", df["trade_date"].min().date(), "至", df["trade_date"].max().date())
    print("有效样本数:", len(df))
    print("最佳候选阈值:", int(best["threshold"]))
    print("最佳候选止盈:", f"{best['take_profit_target']:.1%}")
    print("交易次数:", int(best["trade_count_total"]))
    print("总买入金额:", f"{best['total_buy_amount_all_trades']:.0f}")
    print("已完成盈利:", f"{best['total_profit_closed']:.2f}")
    print("已完成平均收益率:", f"{best['avg_return_closed']:.2%}")
    print("平均持仓天数:", f"{best['avg_holding_days_closed']:.1f}")
    print("最大持仓内回撤:", f"{best['worst_position_drawdown']:.2%}")


if __name__ == "__main__":
    main()
