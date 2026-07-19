#!/usr/bin/env python3
"""Cash-account Flex backtest: capital 50_000, each signal buys 1_000 CNY.

Rules aligned with Flex long-only high-conviction satellite:
  - Core: 60<=RT<80 and dd60<=-5%, hold 5 days, T+1 open, one position at a time
  - Satellite: stage basket (strict stages), equal-weight names, hold 5 days, non-overlap
  - Each new open: allocate min(ORDER_SIZE, available cash) notional
  - Cost: 3bp one-way default (also report 15bp stress)
  - Sector returns via SW L1 / HSTECH index proxies (not true ETF)

Run:
  python3 research/backtest_flex_cash_50k.py
"""
from __future__ import annotations

import json
import math
import sys
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.backtest_core_plus_sectors import (  # noqa: E402
    HOLD_DAYS,
    OOS_SPLIT,
    STAGE_LONG,
    TRADING_DAYS,
    annualized,
    core_signal,
    detect_stages_row,
    load_aligned,
    max_dd,
)

OUT = ROOT / "research/output/core_plus_sectors"
INITIAL_CASH = 50_000.0
ORDER_SIZE = 1_000.0
BUY_COST = 0.0003
SELL_COST = 0.0003

SAT_PRIORITY = [
    "CSI300_CORE_BUY",
    "HIGH_COOLING",
    "ENTER_70_BOUNCE",
    "RISING_HARD",
    "FALLING_HARD",
]


@dataclass
class Lot:
    sleeve: str
    name: str
    stage: str
    entry_i: int
    exit_i: int
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    notional: float
    shares: float  # notional / entry_price (index units)
    entry_price: float
    exit_price: float
    pnl: float
    ret: float


def open_price(opens: np.ndarray, closes: np.ndarray, i: int) -> float | None:
    if i < 0 or i >= len(opens):
        return None
    px = opens[i]
    if not np.isfinite(px) or px <= 0:
        px = closes[i] if i < len(closes) else np.nan
    if not np.isfinite(px) or px <= 0:
        return None
    return float(px)


def run_cash(
    df: pd.DataFrame,
    meta: dict,
    *,
    initial_cash: float = INITIAL_CASH,
    order_size: float = ORDER_SIZE,
    buy_cost: float = BUY_COST,
    sell_cost: float = SELL_COST,
) -> dict:
    n = len(df)
    dates = pd.to_datetime(df["trade_date"])
    csi_open = df["csi_open"].to_numpy(dtype=float)
    csi_close = df["csi_close"].to_numpy(dtype=float)
    sector_open = meta["sector_open"]
    sector_close = meta["sector_close"]

    cash = float(initial_cash)
    lots: list[Lot] = []
    open_core: Lot | None = None
    open_sat: list[Lot] = []
    equity_curve = []
    cash_curve = []
    exposure_curve = []

    def mtm_value(i: int) -> float:
        total = cash
        active = ([open_core] if open_core else []) + list(open_sat)
        for lot in active:
            if lot is None:
                continue
            if lot.sleeve == "core":
                px = csi_close[i] if np.isfinite(csi_close[i]) else lot.entry_price
            else:
                arr = sector_close.get(lot.name)
                px = arr[i] if arr is not None and np.isfinite(arr[i]) else lot.entry_price
            total += lot.shares * float(px)
        return total

    def close_lot(lot: Lot, i: int) -> Lot:
        nonlocal cash
        if lot.sleeve == "core":
            px = open_price(csi_open, csi_close, i)
        else:
            px = open_price(sector_open[lot.name], sector_close[lot.name], i)
        if px is None:
            px = lot.entry_price
        proceeds = lot.shares * px * (1 - sell_cost)
        cost_basis = lot.notional  # already paid at entry incl buy cost approx
        # entry spent = notional * (1+buy_cost) effectively; we sized shares = notional/entry*(1+buy)
        pnl = proceeds - lot.notional * (1 + buy_cost)
        ret = proceeds / (lot.notional * (1 + buy_cost)) - 1
        cash += proceeds
        lot.exit_i = i
        lot.exit_date = pd.Timestamp(dates.iloc[i])
        lot.exit_price = float(px)
        lot.pnl = float(pnl)
        lot.ret = float(ret)
        lots.append(lot)
        return lot

    next_core_free = 0
    next_sat_free = 0

    for i in range(n):
        # --- exits first (at open) ---
        if open_core is not None and i >= open_core.exit_i:
            close_lot(open_core, i)
            open_core = None
            next_core_free = i + 1

        still_sat = []
        for lot in open_sat:
            if i >= lot.exit_i:
                close_lot(lot, i)
                next_sat_free = max(next_sat_free, i + 1)
            else:
                still_sat.append(lot)
        open_sat = still_sat

        # --- signals at close of day i → enter next open i+1 ---
        if i >= n - 2:
            equity_curve.append(mtm_value(i))
            cash_curve.append(cash)
            exposure_curve.append(mtm_value(i) - cash)
            continue

        row = df.iloc[i]
        entry_i = i + 1
        exit_i = min(entry_i + HOLD_DAYS, n - 1)

        # Core
        if open_core is None and entry_i >= next_core_free and core_signal(row):
            px = open_price(csi_open, csi_close, entry_i)
            gross = min(order_size, cash / (1 + buy_cost)) if cash > 0 else 0.0
            if px is not None and gross >= 50:
                shares = gross / px
                cash -= gross * (1 + buy_cost)
                open_core = Lot(
                    sleeve="core",
                    name="沪深300",
                    stage="CSI300_CORE_BUY",
                    entry_i=entry_i,
                    exit_i=exit_i,
                    entry_date=pd.Timestamp(dates.iloc[entry_i]),
                    exit_date=pd.Timestamp(dates.iloc[exit_i]),
                    notional=float(gross),
                    shares=float(shares),
                    entry_price=float(px),
                    exit_price=float("nan"),
                    pnl=0.0,
                    ret=0.0,
                )

        # Satellite basket
        if not open_sat and entry_i >= next_sat_free:
            stages = detect_stages_row(row)
            stage = next((s for s in SAT_PRIORITY if s in stages and s in STAGE_LONG), None)
            if stage:
                names = [nm for nm in STAGE_LONG[stage] if nm in sector_open]
                # filter tradable prices
                tradable = []
                for nm in names:
                    px = open_price(sector_open[nm], sector_close[nm], entry_i)
                    if px is not None:
                        tradable.append((nm, px))
                if tradable:
                    # one signal → 1000 CNY total split equally across names
                    basket_budget = min(order_size, cash / (1 + buy_cost))
                    if basket_budget >= 50:
                        per = basket_budget / len(tradable)
                        opened = []
                        for nm, px in tradable:
                            if cash < per * (1 + buy_cost):
                                break
                            shares = per / px
                            cash -= per * (1 + buy_cost)
                            opened.append(
                                Lot(
                                    sleeve="satellite",
                                    name=nm,
                                    stage=stage,
                                    entry_i=entry_i,
                                    exit_i=exit_i,
                                    entry_date=pd.Timestamp(dates.iloc[entry_i]),
                                    exit_date=pd.Timestamp(dates.iloc[exit_i]),
                                    notional=float(per),
                                    shares=float(shares),
                                    entry_price=float(px),
                                    exit_price=float("nan"),
                                    pnl=0.0,
                                    ret=0.0,
                                )
                            )
                        open_sat = opened

        equity_curve.append(mtm_value(i))
        cash_curve.append(cash)
        exposure_curve.append(mtm_value(i) - cash)

    # force close any remaining at last bar
    if open_core is not None:
        close_lot(open_core, n - 1)
        open_core = None
    for lot in list(open_sat):
        close_lot(lot, n - 1)
    open_sat = []

    equity = np.array(equity_curve, dtype=float)
    # rebuild equity fully after forced closes
    final_equity = cash  # all flat
    # Actually after forced close, cash is full equity
    equity[-1] = cash

    total_pnl = cash - initial_cash
    total_return = cash / initial_cash - 1
    rets = [t.ret for t in lots]
    wins = [r for r in rets if r > 0]
    core_lots = [t for t in lots if t.sleeve == "core"]
    sat_lots = [t for t in lots if t.sleeve == "satellite"]

    # daily equity returns for sharpe
    eq = np.array(equity_curve, dtype=float)
    eq[-1] = cash
    dr = np.diff(eq) / np.where(eq[:-1] == 0, np.nan, eq[:-1])
    dr = dr[np.isfinite(dr)]

    # buy & hold CSI300 with full capital
    c0 = open_price(csi_open, csi_close, 0) or float(csi_close[0])
    c1 = float(csi_close[-1])
    bh_ret = (c1 * (1 - sell_cost)) / (c0 * (1 + buy_cost)) - 1

    # OOS from 2024
    oos_mask = dates >= OOS_SPLIT
    if oos_mask.any():
        first_oos = int(np.argmax(oos_mask.to_numpy()))
        # equity at OOS start
        eq_oos0 = equity_curve[first_oos] if first_oos < len(equity_curve) else initial_cash
        oos_lots = [t for t in lots if t.entry_date >= OOS_SPLIT]
        oos_pnl = sum(t.pnl for t in oos_lots)
        # approximate OOS return on capital at OOS start
        oos_ret = (cash - eq_oos0) / eq_oos0 if eq_oos0 > 0 else float("nan")
        # better: mark equity start of OOS to final
        oos_total_ret = cash / eq_oos0 - 1 if eq_oos0 > 0 else float("nan")
    else:
        oos_lots, oos_pnl, oos_total_ret, eq_oos0 = [], 0.0, float("nan"), float("nan")

    # peak exposure
    max_exposure = float(np.nanmax(exposure_curve)) if exposure_curve else 0.0
    avg_exposure = float(np.nanmean(exposure_curve)) if exposure_curve else 0.0

    return {
        "params": {
            "initial_cash": initial_cash,
            "order_size": order_size,
            "buy_cost": buy_cost,
            "sell_cost": sell_cost,
            "hold_days": HOLD_DAYS,
            "execution": "T close signal → T+1 open",
            "satellite_budget": "1000 CNY per signal split equally across basket names",
            "core_budget": "1000 CNY per core signal",
            "proxy_note": "行业指数代理 ETF，非真实 ETF 成交",
        },
        "summary": {
            "start_date": str(dates.iloc[0].date()),
            "end_date": str(dates.iloc[-1].date()),
            "n_days": n,
            "final_equity": float(cash),
            "total_pnl": float(total_pnl),
            "total_return": float(total_return),
            "ann_return": float(annualized(total_return, n)),
            "max_dd": float(max_dd(eq / eq[0])) if len(eq) and eq[0] > 0 else float("nan"),
            "trade_count": len(lots),
            "core_trades": len(core_lots),
            "sat_trades": len(sat_lots),
            "win_rate": float(np.mean([r > 0 for r in rets])) if rets else float("nan"),
            "avg_trade_return": float(np.mean(rets)) if rets else float("nan"),
            "sum_trade_pnl": float(sum(t.pnl for t in lots)),
            "profit_factor": float(sum(wins) / abs(sum(r for r in rets if r <= 0)))
            if any(r <= 0 for r in rets) and abs(sum(r for r in rets if r <= 0)) > 0
            else float("nan"),
            "max_exposure": max_exposure,
            "avg_exposure": avg_exposure,
            "avg_exposure_pct_of_capital": avg_exposure / initial_cash,
            "max_exposure_pct_of_capital": max_exposure / initial_cash,
            "sharpe": float(np.mean(dr) / np.std(dr, ddof=1) * math.sqrt(TRADING_DAYS))
            if len(dr) > 2 and np.std(dr, ddof=1) > 0
            else float("nan"),
            "buy_hold_csi300_return": float(bh_ret),
            "buy_hold_final": float(initial_cash * (1 + bh_ret)),
            "excess_vs_bh": float(total_return - bh_ret),
        },
        "oos": {
            "split": str(OOS_SPLIT.date()),
            "equity_at_split": float(eq_oos0) if eq_oos0 == eq_oos0 else None,
            "final_equity": float(cash),
            "total_return_from_split": float(oos_total_ret) if oos_total_ret == oos_total_ret else None,
            "trade_count": len(oos_lots),
            "trade_pnl": float(oos_pnl),
            "win_rate": float(np.mean([t.ret > 0 for t in oos_lots])) if oos_lots else None,
        },
        "by_sleeve": {
            "core": {
                "n": len(core_lots),
                "pnl": float(sum(t.pnl for t in core_lots)),
                "win_rate": float(np.mean([t.ret > 0 for t in core_lots])) if core_lots else None,
                "avg_ret": float(np.mean([t.ret for t in core_lots])) if core_lots else None,
            },
            "satellite": {
                "n": len(sat_lots),
                "pnl": float(sum(t.pnl for t in sat_lots)),
                "win_rate": float(np.mean([t.ret > 0 for t in sat_lots])) if sat_lots else None,
                "avg_ret": float(np.mean([t.ret for t in sat_lots])) if sat_lots else None,
            },
        },
        "lots": [asdict(t) for t in lots],
        "equity_curve": {
            "trade_date": [str(d.date()) for d in dates],
            "equity": [float(x) for x in eq],
            "cash": [float(x) for x in cash_curve],
            "exposure": [float(x) for x in exposure_curve],
        },
    }


def write_report(res: dict) -> Path:
    s = res["summary"]
    o = res["oos"]
    p = res["params"]
    cs = res["by_sleeve"]["core"]
    ss = res["by_sleeve"]["satellite"]

    def pct(x):
        try:
            return f"{float(x)*100:.2f}%"
        except Exception:
            return "--"

    def money(x):
        try:
            return f"{float(x):,.2f}"
        except Exception:
            return "--"

    lines = [
        "# 组合 Flex 现金账户回测（5万本金 / 每信号 1000）",
        "",
        "## 设定",
        "",
        f"- 样本: **{s['start_date']} → {s['end_date']}**（{s['n_days']} 交易日）",
        f"- 初始资金: **{money(p['initial_cash'])} 元**",
        f"- 每次信号买入: **{money(p['order_size'])} 元**（核心每信号 1000；卫星每信号 1000 等权分到篮子成分）",
        f"- 持有: **{p['hold_days']}** 交易日 · 执行: {p['execution']}",
        f"- 成本: 单边 {p['buy_cost']*10000:.1f} bp",
        f"- 说明: {p['proxy_note']}",
        f"- 同一时间核心最多 1 笔；卫星篮子不重叠开仓",
        "",
        "## 总收益",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 期末权益 | **{money(s['final_equity'])} 元** |",
        f"| 总盈亏 | **{money(s['total_pnl'])} 元** |",
        f"| 总收益率 | **{pct(s['total_return'])}** |",
        f"| 年化收益 | **{pct(s['ann_return'])}** |",
        f"| 最大回撤 | {pct(s['max_dd'])} |",
        f"| 交易笔数 | {s['trade_count']}（核心 {s['core_trades']} / 卫星腿 {s['sat_trades']}） |",
        f"| 胜率 | {pct(s['win_rate'])} |",
        f"| 平均单笔收益 | {pct(s['avg_trade_return'])} |",
        f"| 平均暴露 | {money(s['avg_exposure'])}（约占本金 {pct(s['avg_exposure_pct_of_capital'])}） |",
        f"| 峰值暴露 | {money(s['max_exposure'])}（{pct(s['max_exposure_pct_of_capital'])}） |",
        f"| Sharpe | {s['sharpe']:.2f}" if s.get("sharpe") == s.get("sharpe") else "| Sharpe | -- |",
        f"| 同期满仓沪深300 | {pct(s['buy_hold_csi300_return'])}（期末约 {money(s['buy_hold_final'])}） |",
        f"| 相对满仓超额 | {pct(s['excess_vs_bh'])} |",
        "",
        "## 分仓贡献",
        "",
        "| 袖套 | 笔数 | 盈亏(元) | 胜率 | 平均单笔收益 |",
        "|------|-----:|---------:|-----:|-------------:|",
        f"| 核心沪深300 | {cs['n']} | {money(cs['pnl'])} | {pct(cs['win_rate'])} | {pct(cs['avg_ret'])} |",
        f"| 卫星板块腿 | {ss['n']} | {money(ss['pnl'])} | {pct(ss['win_rate'])} | {pct(ss['avg_ret'])} |",
        "",
        "## 样本外（自 2024-01-01）",
        "",
        f"- 切分时权益: {money(o.get('equity_at_split'))}",
        f"- 切分→期末收益率: **{pct(o.get('total_return_from_split'))}**",
        f"- OOS 交易笔数: {o.get('trade_count')} · 交易盈亏 {money(o.get('trade_pnl'))} · 胜率 {pct(o.get('win_rate'))}",
        "",
        "## 怎么理解这个收益率",
        "",
        "1. **不是满仓 Flex**：每次只动 1000 元，5 万里大部分时间是现金，所以绝对收益率会明显低于「全仓回测年化 10–30%」那套数字。",
        "2. **有意义的是**：这 1000 元信号资金本身的胜率/单笔收益，以及期末总盈亏。",
        "3. 卫星用行业指数代理，实盘 ETF 会更差一些；成本若按 15bp 会再打折。",
        "4. 非投资建议。",
        "",
    ]
    path = OUT / "flex_cash_50k_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    warnings.filterwarnings("ignore")
    OUT.mkdir(parents=True, exist_ok=True)
    print("Loading...")
    df, meta = load_aligned()
    print(f"n={len(df)} {df.trade_date.min().date()} → {df.trade_date.max().date()}")

    res = run_cash(df, meta, buy_cost=BUY_COST, sell_cost=SELL_COST)
    stress = run_cash(df, meta, buy_cost=0.0015, sell_cost=0.0015)

    # save
    (OUT / "flex_cash_50k_summary.json").write_text(
        json.dumps(
            {
                "base_3bps": {"params": res["params"], "summary": res["summary"], "oos": res["oos"], "by_sleeve": res["by_sleeve"]},
                "stress_15bps": {"summary": stress["summary"], "oos": stress["oos"], "by_sleeve": stress["by_sleeve"]},
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "sleeve": t["sleeve"],
                "name": t["name"],
                "stage": t["stage"],
                "entry_date": str(t["entry_date"])[:10],
                "exit_date": str(t["exit_date"])[:10],
                "notional": t["notional"],
                "entry_price": t["entry_price"],
                "exit_price": t["exit_price"],
                "ret": t["ret"],
                "pnl": t["pnl"],
            }
            for t in res["lots"]
        ]
    ).to_csv(OUT / "flex_cash_50k_trades.csv", index=False)

    eq = pd.DataFrame(
        {
            "trade_date": res["equity_curve"]["trade_date"],
            "equity": res["equity_curve"]["equity"],
            "cash": res["equity_curve"]["cash"],
            "exposure": res["equity_curve"]["exposure"],
        }
    )
    eq.to_csv(OUT / "flex_cash_50k_equity.csv", index=False)
    report = write_report(res)

    s = res["summary"]
    print("=" * 60)
    print("组合 Flex 现金账户回测")
    print(f"本金 {INITIAL_CASH:.0f} · 每信号 {ORDER_SIZE:.0f}")
    print(f"区间 {s['start_date']} → {s['end_date']}")
    print(f"期末权益 {s['final_equity']:.2f}  盈亏 {s['total_pnl']:.2f}  收益率 {s['total_return']*100:.2f}%")
    print(f"年化 {s['ann_return']*100:.2f}%  最大回撤 {s['max_dd']*100:.2f}%  胜率 {s['win_rate']*100:.1f}%  笔数 {s['trade_count']}")
    print(f"核心盈亏 {res['by_sleeve']['core']['pnl']:.2f}  卫星盈亏 {res['by_sleeve']['satellite']['pnl']:.2f}")
    print(f"满仓沪深300同期 {s['buy_hold_csi300_return']*100:.2f}%")
    print(f"OOS 收益 {res['oos'].get('total_return_from_split')}")
    print(f"15bp 压力期末 {stress['summary']['final_equity']:.2f} 收益 {stress['summary']['total_return']*100:.2f}%")
    print("Report", report)


if __name__ == "__main__":
    main()
