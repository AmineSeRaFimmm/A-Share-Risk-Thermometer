#!/usr/bin/env python3
"""Strict cash-ledger backtest: Flex 进取 + hold-to-profit + 30k/30k pools.

Rules (confirmed by user):
  - Flex 进取 entry signals (core 60≤RT<80 & dd≤-5% H5; sat stage-driven)
  - Capital: primary 30_000 (首仓) + reserve 30_000 (补仓/新开), total 60_000
  - At planned exit: if lot still at a loss vs cost (after sell cost) → do NOT sell;
    hold until mark-to-market profit at open, then sell
  - While any position open (incl. extension): new buy signals may ADD or OPEN NEW
    names using reserve (and leftover primary), sized by Flex weights proportionally
  - No max hold / hard stop
  - Cost 3bp each side; real prices for profit check (no research haircut on exit decision)

Comparisons on same window:
  A) Baseline Flex 进取 force-exit, capital 60k fully following % path
  B) Baseline Flex 进取 force-exit, only 30k risked + 30k idle cash
  C) This rule: 30k+30k pools, hold-to-profit, multi-lot, 可新开
  D) CSI300 buy&hold on 60k
"""
from __future__ import annotations

import json
import math
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.backtest_core_plus_sectors import (  # noqa: E402
    TRADING_DAYS,
    annualized,
    detect_stages_row,
    load_aligned,
    max_dd,
)
from research.backtest_flex_dense_grid import (  # noqa: E402
    combine_port,
    extend_aligned_to_end,
    precompute_stages,
    simulate_core_daily,
    simulate_sat_daily,
    stats_from_daily,
)
from src.core.flex_engine import (  # noqa: E402
    QUALITY_WEIGHT,
    STAGE_OPPOSITES,
    STAGE_TIER,
    merge_satellite_targets,
)
from src.core.sector_etf_map import map_sector  # noqa: E402

OUT = ROOT / "research/output/flex_hold_to_profit"
START = pd.Timestamp("2025-05-01")
END = pd.Timestamp("2026-07-13")
EXT_END = pd.Timestamp("2026-07-14")

PRIMARY0 = 30_000.0
RESERVE0 = 30_000.0
TOTAL0 = PRIMARY0 + RESERVE0
COST = 0.0003  # 3bp
CORE_HOLD = 5
SAT_MIN, SAT_DEFAULT, SAT_MAX = 3, 5, 8
W_CORE, W_SAT = 0.6, 0.4


def quality_of(name: str) -> str:
    return str(map_sector(name).get("quality") or "missing")


@dataclass
class Lot:
    lot_id: int
    sleeve: str  # core | sat
    name: str
    shares: float
    cost_total: float  # cash spent incl buy cost
    from_primary: float
    from_reserve: float
    entry_i: int
    planned_exit_i: int
    entry_px: float
    extended_days: int = 0
    closed: bool = False
    exit_i: int | None = None
    exit_px: float | None = None
    pnl: float | None = None
    exit_reason: str = ""


@dataclass
class PendingOrder:
    sleeve: str
    name: str
    notional: float  # cash to spend (gross before distinguishing pools)
    use_primary: float
    use_reserve: float
    planned_hold: int
    signal_i: int


@dataclass
class SimResult:
    tag: str
    equity: np.ndarray
    dates: pd.Series
    lots: list[Lot]
    events: list[dict]
    daily_ret: np.ndarray
    stats: dict
    extra: dict = field(default_factory=dict)


def px_open(name: str, i: int, csi_open: np.ndarray, sector_open: dict[str, np.ndarray]) -> float:
    if name == "CSI300":
        return float(csi_open[i]) if np.isfinite(csi_open[i]) else float("nan")
    arr = sector_open.get(name)
    if arr is None or i >= len(arr):
        return float("nan")
    return float(arr[i]) if np.isfinite(arr[i]) else float("nan")


def px_close(name: str, i: int, csi_close: np.ndarray, sector_close: dict[str, np.ndarray]) -> float:
    if name == "CSI300":
        return float(csi_close[i]) if np.isfinite(csi_close[i]) else float("nan")
    arr = sector_close.get(name)
    if arr is None or i >= len(arr):
        return float("nan")
    return float(arr[i]) if np.isfinite(arr[i]) else float("nan")


def sat_targets_at(
    stages: list[str],
    stages_all: list[list[str]],
    sector_open: dict,
    i: int,
) -> list[dict]:
    rising = "RISING_HARD" in stages
    longs, _av, _sup = merge_satellite_targets(list(stages), rising_hard=rising)
    high = [s for s in stages if STAGE_TIER.get(s) == "high"]
    obs = [s for s in stages if STAGE_TIER.get(s) == "observe"]
    if not longs or (not high and not obs):
        return []
    if not high and obs:
        longs = longs[:1]
    use = [
        x
        for x in longs
        if x["name"] in sector_open and QUALITY_WEIGHT.get(quality_of(x["name"]), 0) > 0
    ]
    return use


def sat_planned_exit(
    stages_all: list[list[str]],
    entry_i: int,
    primary_stage: str,
    n: int,
) -> int:
    exit_i = min(entry_i + SAT_DEFAULT, n - 1)
    for k in range(entry_i + SAT_MIN, min(entry_i + SAT_MAX, n - 1) + 1):
        st_sig = stages_all[k - 1] if k - 1 >= 0 else stages_all[min(k, n - 1)]
        held = k - entry_i
        opposites = STAGE_OPPOSITES.get(primary_stage, set())
        if held >= SAT_MIN and opposites.intersection(st_sig):
            return k
        if held >= SAT_MAX:
            return k
        if held >= SAT_DEFAULT and not any(STAGE_TIER.get(s) in {"high", "observe"} for s in st_sig):
            return k
    return min(entry_i + SAT_MAX, n - 1)


def core_signal_day(rt: float, dd: float) -> bool:
    return bool(np.isfinite(rt) and np.isfinite(dd) and 60 <= rt < 80 and dd <= -0.05)


def equity_stats(eq: np.ndarray, daily: np.ndarray, n_trades: int = 0, win_rate: float = float("nan")) -> dict:
    total = float(eq[-1] / eq[0] - 1.0) if len(eq) > 1 and eq[0] > 0 else 0.0
    n = len(daily)
    return {
        "total_return": total,
        "ann_return": float(annualized(total, n)) if n else float("nan"),
        "max_dd": float(max_dd(eq)) if len(eq) else float("nan"),
        "sharpe": float(np.mean(daily) / np.std(daily, ddof=1) * math.sqrt(TRADING_DAYS))
        if n > 2 and np.std(daily, ddof=1) > 0
        else float("nan"),
        "n_days": n,
        "trade_count": n_trades,
        "win_rate": win_rate,
        "final_equity": float(eq[-1]) if len(eq) else float("nan"),
        "exposure": float(np.mean(daily != 0)) if n else 0.0,  # approx; overwritten if needed
    }


def window_mask(dates: pd.Series) -> np.ndarray:
    return (dates.values >= np.datetime64(START)) & (dates.values <= np.datetime64(END))


def slice_window(eq_full: np.ndarray, daily_full: np.ndarray, dates: pd.Series) -> tuple[np.ndarray, np.ndarray, pd.Series]:
    m = window_mask(dates)
    d = daily_full[m]
    # rebuild equity from 1.0 in window
    eq = np.cumprod(1.0 + d)
    # scale to capital
    return eq, d, dates[m].reset_index(drop=True)


# ---------- Baseline % path Flex 进取 ----------
def baseline_flex_pct(
    df: pd.DataFrame,
    meta: dict,
    stages_all: list[list[str]],
    capital: float,
    risk_capital: float | None = None,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """daily portfolio returns; if risk_capital < capital, idle cash dilutes."""
    rt = df["rt"].to_numpy(dtype=float)
    dd = df["dd60"].to_numpy(dtype=float)
    csi_open = df["csi_open"].to_numpy(dtype=float)
    csi_close = df["csi_close"].to_numpy(dtype=float)
    core_d, _ = simulate_core_daily(
        rt, dd, csi_open, csi_close,
        rt_low=60, rt_high=80, dd_max=-0.05, hold_days=CORE_HOLD,
        buy_cost=COST, sell_cost=COST,
    )
    sat_d, _ = simulate_sat_daily(
        df, meta, stages_all,
        sat_min=SAT_MIN, sat_default=SAT_DEFAULT, sat_max=SAT_MAX,
        buy_cost=COST, sell_cost=COST,
        apply_haircut=False, event_exit=True,
    )
    port = combine_port(
        core_d, sat_d,
        w_core=W_CORE, w_sat=W_SAT, total_cap=1.0, flex_single_full=True,
    )
    if risk_capital is None:
        risk_capital = capital
    scale = risk_capital / capital
    daily = port * scale
    m = window_mask(df["trade_date"])
    d_w = daily[m]
    eq = capital * np.cumprod(1.0 + d_w)
    # prepend start capital point: cumprod starts day1 end
    st = stats_from_daily(d_w)
    st["final_equity"] = float(eq[-1]) if len(eq) else capital
    st["start_capital"] = capital
    st["risk_capital"] = risk_capital
    exp = float(np.mean(np.abs(d_w) > 1e-15))
    st["exposure"] = exp
    return eq, d_w, st


# ---------- Cash lot simulator ----------
def simulate_cash_ledger(
    df: pd.DataFrame,
    meta: dict,
    stages_all: list[list[str]],
    *,
    hold_to_profit: bool,
    allow_pyramid_new: bool,
    primary0: float = PRIMARY0,
    reserve0: float = RESERVE0,
    tag: str = "rule",
) -> SimResult:
    """
    hold_to_profit=False, allow_pyramid_new=False → approximate baseline with cash
      (force exit at planned, ignore signals while sleeve has open lots).
    hold_to_profit=True, allow_pyramid_new=True → user strategy.
    """
    dates = df["trade_date"]
    n = len(df)
    rt = df["rt"].to_numpy(dtype=float)
    dd = df["dd60"].to_numpy(dtype=float)
    csi_open = df["csi_open"].to_numpy(dtype=float)
    csi_close = df["csi_close"].to_numpy(dtype=float)
    sector_open = meta["sector_open"]
    sector_close = meta["sector_close"]

    cash_p = float(primary0)
    cash_r = float(reserve0)
    lots: list[Lot] = []
    events: list[dict] = []
    pending: list[PendingOrder] = []
    next_lot_id = 1

    # equity series only stored for window days
    eq_by_i = np.full(n, np.nan)
    pos_value_by_i = np.full(n, np.nan)

    def open_lots() -> list[Lot]:
        return [L for L in lots if not L.closed]

    def mtm_value(i: int) -> float:
        v = 0.0
        for L in open_lots():
            px = px_close(L.name, i, csi_close, sector_close)
            if not np.isfinite(px):
                px = px_open(L.name, i, csi_open, sector_open)
            if np.isfinite(px):
                v += L.shares * px
        return v

    def nav_at_close(i: int) -> float:
        return cash_p + cash_r + mtm_value(i)

    def sleeve_has_open(sleeve: str) -> bool:
        return any(not L.closed and L.sleeve == sleeve for L in lots)

    def any_extended(i: int) -> bool:
        return any(not L.closed and i >= L.planned_exit_i for L in lots)

    def any_open() -> bool:
        return bool(open_lots())

    def try_sell_lot(L: Lot, i: int, reason: str) -> bool:
        nonlocal cash_p, cash_r
        px = px_open(L.name, i, csi_open, sector_open)
        if not np.isfinite(px) or px <= 0:
            return False
        proceeds = L.shares * px * (1.0 - COST)
        # profit vs cost
        pnl = proceeds - L.cost_total
        if hold_to_profit and i >= L.planned_exit_i and pnl <= 0 and reason != "EOD_FORCE":
            L.extended_days += 1
            return False
        # execute sell
        # return cash proportional to funding source
        tot_src = L.from_primary + L.from_reserve
        if tot_src <= 0:
            fp, fr = 1.0, 0.0
        else:
            fp, fr = L.from_primary / tot_src, L.from_reserve / tot_src
        cash_p += proceeds * fp
        cash_r += proceeds * fr
        L.closed = True
        L.exit_i = i
        L.exit_px = px
        L.pnl = float(pnl)
        L.exit_reason = reason if not (hold_to_profit and L.extended_days > 0 and reason == "PLANNED") else (
            "PROFIT_AFTER_EXTEND" if pnl > 0 else reason
        )
        if L.extended_days > 0 and pnl > 0 and reason in ("PLANNED", "DAILY_PROFIT_CHECK"):
            L.exit_reason = "PROFIT_AFTER_EXTEND"
        events.append(
            {
                "type": "SELL",
                "date": str(dates.iloc[i].date()),
                "lot_id": L.lot_id,
                "sleeve": L.sleeve,
                "name": L.name,
                "px": px,
                "proceeds": proceeds,
                "pnl": float(pnl),
                "extended_days": L.extended_days,
                "reason": L.exit_reason,
            }
        )
        return True

    def process_exits(i: int) -> None:
        for L in open_lots():
            if i < L.planned_exit_i:
                continue
            if hold_to_profit:
                # every day from planned_exit onward: sell only if profitable
                try_sell_lot(L, i, "DAILY_PROFIT_CHECK" if i > L.planned_exit_i else "PLANNED")
            else:
                try_sell_lot(L, i, "PLANNED")

    def allocate_buy_budget(want_core: bool, want_sat: bool, pyramid_mode: bool) -> tuple[float, float, float]:
        """Return (total_budget, from_p, from_r) for today's entries."""
        nonlocal cash_p, cash_r
        if not want_core and not want_sat:
            return 0.0, 0.0, 0.0
        if pyramid_mode and allow_pyramid_new:
            # 补仓优先用 reserve；primary 剩余也可动用
            budget = cash_r + (cash_p if cash_p > 0 else 0.0)
            # Prefer reserve first
            use_r = min(cash_r, budget)
            use_p = min(cash_p, max(0.0, budget - use_r))
            # When pyramid, default spend all reserve available (全仓补仓池), primary only if reserve insufficient?
            # User: 3万补仓 for adds. Use all free reserve; don't auto-drain primary unless primary idle and flat sleeves need it.
            use_p = 0.0  # pyramid/new-open while invested: reserve only
            use_r = cash_r
            return use_p + use_r, use_p, use_r
        # Fresh / baseline: use primary only for 首仓 (3万)
        use_p = cash_p
        use_r = 0.0
        return use_p + use_r, use_p, use_r

    def execute_buys(i: int) -> None:
        nonlocal cash_p, cash_r, next_lot_id, pending
        if not pending:
            return
        # pending created on signal day i-1 for entry i
        orders = pending
        pending = []
        # filter executable
        live = []
        for o in orders:
            px = px_open(o.name, i, csi_open, sector_open)
            if np.isfinite(px) and px > 0 and o.notional > 1.0:
                live.append((o, px))
        if not live:
            return
        for o, px in live:
            spend = o.use_primary + o.use_reserve
            if spend <= 1.0:
                continue
            # clamp to available cash
            up = min(o.use_primary, cash_p)
            ur = min(o.use_reserve, cash_r)
            spend = up + ur
            if spend <= 1.0:
                continue
            shares = (spend / (1.0 + COST)) / px
            cost_total = shares * px * (1.0 + COST)
            # adjust if float drift
            if cost_total > cash_p + cash_r:
                scale = (cash_p + cash_r) / cost_total * 0.999
                shares *= scale
                cost_total = shares * px * (1.0 + COST)
                up = min(up, cash_p)
                ur = cost_total - up
                if ur > cash_r:
                    ur = cash_r
                    up = cost_total - ur
            cash_p -= up
            cash_r -= ur
            if cash_p < -1e-6 or cash_r < -1e-6:
                # rollback safety
                cash_p += up
                cash_r += ur
                continue
            planned_exit = min(i + o.planned_hold, n - 1)
            if o.sleeve == "sat":
                # planned_hold already absolute offset days
                planned_exit = min(i + o.planned_hold, n - 1)
            L = Lot(
                lot_id=next_lot_id,
                sleeve=o.sleeve,
                name=o.name,
                shares=shares,
                cost_total=cost_total,
                from_primary=up,
                from_reserve=ur,
                entry_i=i,
                planned_exit_i=planned_exit,
                entry_px=px,
            )
            next_lot_id += 1
            lots.append(L)
            events.append(
                {
                    "type": "BUY",
                    "date": str(dates.iloc[i].date()),
                    "lot_id": L.lot_id,
                    "sleeve": L.sleeve,
                    "name": L.name,
                    "px": px,
                    "notional": cost_total,
                    "from_primary": up,
                    "from_reserve": ur,
                    "planned_exit": str(dates.iloc[planned_exit].date()),
                    "signal_date": str(dates.iloc[o.signal_i].date()),
                }
            )

    def queue_signals(i: int) -> None:
        """At close of day i, queue orders for open i+1."""
        nonlocal pending
        if i >= n - 2:
            return
        want_core = core_signal_day(rt[i], dd[i])
        stages = stages_all[i]
        sat_use = sat_targets_at(stages, stages_all, sector_open, i)
        want_sat = len(sat_use) > 0

        if not want_core and not want_sat:
            return

        # Baseline: ignore signal if sleeve already has open lots
        if not allow_pyramid_new:
            if want_core and sleeve_has_open("core"):
                want_core = False
            if want_sat and sleeve_has_open("sat"):
                want_sat = False
            if not want_core and not want_sat:
                return
            pyramid = False
        else:
            # User rule: if flat → 首仓 primary; if any open → 可新开/加仓 with reserve
            pyramid = any_open()
            # If flat, only primary. If open, can still use primary leftovers only when not pyramid-only...
            # When flat: open with primary.
            # When open: new signals use reserve (可新开).

        budget, _, _ = allocate_buy_budget(want_core, want_sat, pyramid_mode=pyramid)
        if budget < 10:
            return

        # Flex single-full weights on budget
        if want_core and want_sat:
            wc, ws = W_CORE, W_SAT
        elif want_core:
            wc, ws = 1.0, 0.0
        else:
            wc, ws = 0.0, 1.0
        core_budget = budget * wc
        sat_budget = budget * ws

        # Funding split: track how much from p vs r for this batch
        if pyramid and allow_pyramid_new:
            fund_p, fund_r = 0.0, min(budget, cash_r)
            # spend only what we have in reserve
            core_budget = fund_r * wc
            sat_budget = fund_r * ws
            total_fund = fund_r
            fp_ratio, fr_ratio = 0.0, 1.0
        else:
            fund_p, fund_r = min(budget, cash_p), 0.0
            total_fund = fund_p
            core_budget = fund_p * wc
            sat_budget = fund_p * ws
            fp_ratio, fr_ratio = 1.0, 0.0

        if total_fund < 10:
            return

        orders: list[PendingOrder] = []
        if want_core and core_budget >= 10:
            orders.append(
                PendingOrder(
                    sleeve="core",
                    name="CSI300",
                    notional=core_budget,
                    use_primary=core_budget * fp_ratio,
                    use_reserve=core_budget * fr_ratio,
                    planned_hold=CORE_HOLD,
                    signal_i=i,
                )
            )
        if want_sat and sat_budget >= 10 and sat_use:
            primary_stage = next(
                (
                    s
                    for s in ["CSI300_CORE_BUY", "HIGH_COOLING", "ENTER_70_BOUNCE", "RISING_HARD", "FALLING_HARD"]
                    if s in stages
                ),
                stages[0] if stages else "",
            )
            entry_i = i + 1
            exit_i = sat_planned_exit(stages_all, entry_i, primary_stage, n)
            hold_days = max(1, exit_i - entry_i)
            weights = np.array([max(float(x.get("weight_in_sat") or 0.0), 1e-6) for x in sat_use], dtype=float)
            weights = weights / weights.sum()
            for x, w in zip(sat_use, weights):
                notion = sat_budget * float(w)
                if notion < 10:
                    continue
                orders.append(
                    PendingOrder(
                        sleeve="sat",
                        name=str(x["name"]),
                        notional=notion,
                        use_primary=notion * fp_ratio,
                        use_reserve=notion * fr_ratio,
                        planned_hold=hold_days,
                        signal_i=i,
                    )
                )
        pending.extend(orders)

    # ---- main loop: only need history for signals; track full then slice ----
    start_i = int(np.where(dates.values >= np.datetime64(START))[0][0]) if np.any(dates.values >= np.datetime64(START)) else 0
    end_i = int(np.where(dates.values <= np.datetime64(END))[0][-1])

    # Trade only on window signals (entries T+1 may fall just after signal in window).
    for i in range(n):
        # 1) open: exits then buys
        if dates.iloc[i] >= START:
            process_exits(i)
            execute_buys(i)
        # 2) close: record nav; queue signals in window
        nav = nav_at_close(i)
        eq_by_i[i] = nav
        pos_value_by_i[i] = mtm_value(i)
        if START <= dates.iloc[i] <= END:
            queue_signals(i)

    # Force mark open lots at end_i open/close for reporting
    for L in open_lots():
        try_sell_lot(L, end_i, "EOD_FORCE")
    if open_lots():
        # if still open (no price), mark at close without sell into cash for final nav
        pass
    eq_by_i[end_i] = nav_at_close(end_i)

    # Window equity path
    m = window_mask(dates)
    idx = np.where(m)[0]
    # Use start-of-window capital: if we had no pre-window trading, nav=60000
    # Recompute clean: only process signals with signal in window — already mostly.
    # Build daily returns from nav series in window
    nav_w = eq_by_i[idx].copy()
    # fill any nan
    for j in range(len(nav_w)):
        if not np.isfinite(nav_w[j]):
            nav_w[j] = nav_w[j - 1] if j else (primary0 + reserve0)
    daily = np.zeros(len(nav_w))
    daily[0] = nav_w[0] / (primary0 + reserve0) - 1.0
    daily[1:] = nav_w[1:] / nav_w[:-1] - 1.0
    eq = nav_w  # absolute equity

    closed = [L for L in lots if L.closed and L.pnl is not None]
    # only lots that entered in window
    win_lots = [
        L
        for L in lots
        if L.entry_i < n and START <= dates.iloc[L.entry_i] <= END
    ]
    closed_win = [L for L in win_lots if L.closed and L.pnl is not None]
    wins = [L for L in closed_win if (L.pnl or 0) > 0]
    extended = [L for L in win_lots if L.extended_days > 0]
    forced = [L for L in closed_win if L.exit_reason == "EOD_FORCE"]
    still_open = [L for L in win_lots if not L.closed]

    st = equity_stats(
        eq if eq[0] > 0 else np.array([primary0 + reserve0]),
        daily,
        n_trades=len(closed_win),
        win_rate=float(len(wins) / len(closed_win)) if closed_win else float("nan"),
    )
    # fix total return vs initial capital
    st["total_return"] = float(eq[-1] / (primary0 + reserve0) - 1.0)
    st["ann_return"] = float(annualized(st["total_return"], len(daily)))
    st["max_dd"] = float(max_dd(eq / eq[0])) if eq[0] > 0 else float(max_dd(eq))
    st["final_equity"] = float(eq[-1])
    st["start_capital"] = primary0 + reserve0
    invested = pos_value_by_i[idx]
    st["exposure"] = float(np.mean(invested > 1.0)) if len(invested) else 0.0
    st["avg_invested"] = float(np.mean(invested)) if len(invested) else 0.0

    extra = {
        "n_lots": len(win_lots),
        "n_closed": len(closed_win),
        "n_wins": len(wins),
        "n_extended": len(extended),
        "avg_extended_days": float(np.mean([L.extended_days for L in extended])) if extended else 0.0,
        "max_extended_days": int(max([L.extended_days for L in extended], default=0)),
        "n_eod_force": len(forced),
        "eod_force_pnl": float(sum(L.pnl or 0 for L in forced)),
        "n_still_open": len(still_open),
        "sum_pnl_closed": float(sum(L.pnl or 0 for L in closed_win)),
        "pyramid_buys": sum(1 for e in events if e["type"] == "BUY" and e.get("from_reserve", 0) > 0),
        "primary_buys": sum(1 for e in events if e["type"] == "BUY" and e.get("from_primary", 0) > 0),
        "final_cash_p": cash_p,
        "final_cash_r": cash_r,
    }

    return SimResult(
        tag=tag,
        equity=eq,
        dates=dates[m].reset_index(drop=True),
        lots=lots,
        events=events,
        daily_ret=daily,
        stats=st,
        extra=extra,
    )


def pct(x) -> str:
    try:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return "—"
        return f"{float(x):.2%}"
    except Exception:
        return "—"


def num(x, nd=2) -> str:
    try:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return "—"
        return f"{float(x):,.{nd}f}"
    except Exception:
        return "—"


def main() -> None:
    warnings.filterwarnings("ignore")
    OUT.mkdir(parents=True, exist_ok=True)
    print("=== Load data ===", flush=True)
    df0, meta0 = load_aligned()
    df, meta, audit = extend_aligned_to_end(df0, meta0, EXT_END)
    df = df[df["trade_date"] <= END].reset_index(drop=True)
    stages_all = precompute_stages(df)
    dates = df["trade_date"]
    print(f"rows={len(df)} window={START.date()}→{END.date()}", flush=True)

    # A) % baseline full 60k
    print("=== A) Baseline Flex 进取 60k full ===", flush=True)
    eq_a, d_a, st_a = baseline_flex_pct(df, meta, stages_all, TOTAL0, risk_capital=TOTAL0)
    # B) 30k risked + 30k idle
    print("=== B) Baseline Flex 进取 30k risked + 30k idle ===", flush=True)
    eq_b, d_b, st_b = baseline_flex_pct(df, meta, stages_all, TOTAL0, risk_capital=PRIMARY0)
    # C) Cash baseline force-exit no pyramid (验证引擎)
    print("=== C) Cash ledger force-exit no-pyramid ===", flush=True)
    sim_base = simulate_cash_ledger(
        df, meta, stages_all,
        hold_to_profit=False,
        allow_pyramid_new=False,
        tag="CASH_BASELINE_FORCE_EXIT",
    )
    # D) User rule
    print("=== D) User rule hold-to-profit + pyramid/new ===", flush=True)
    sim_rule = simulate_cash_ledger(
        df, meta, stages_all,
        hold_to_profit=True,
        allow_pyramid_new=True,
        tag="HOLD_TO_PROFIT_PYRAMID",
    )
    # E) BH 60k
    m = window_mask(dates)
    bh_daily = df["csi_close"].pct_change().fillna(0.0).to_numpy()[m]
    eq_bh = TOTAL0 * np.cumprod(1.0 + bh_daily)
    st_bh = stats_from_daily(bh_daily)
    st_bh["final_equity"] = float(eq_bh[-1])
    st_bh["start_capital"] = TOTAL0

    # Lots / events export
    def lots_df(sim: SimResult) -> pd.DataFrame:
        rows = []
        for L in sim.lots:
            if L.entry_i >= len(df):
                continue
            ed = df["trade_date"].iloc[L.entry_i]
            if ed < START or ed > END:
                # include if exit in window
                if L.exit_i is None:
                    continue
                xd = df["trade_date"].iloc[L.exit_i]
                if xd < START or xd > END:
                    continue
            rows.append(
                {
                    "lot_id": L.lot_id,
                    "sleeve": L.sleeve,
                    "name": L.name,
                    "entry_date": str(df["trade_date"].iloc[L.entry_i].date()),
                    "planned_exit": str(df["trade_date"].iloc[min(L.planned_exit_i, len(df) - 1)].date()),
                    "exit_date": str(df["trade_date"].iloc[L.exit_i].date()) if L.exit_i is not None else "",
                    "entry_px": L.entry_px,
                    "exit_px": L.exit_px,
                    "cost_total": L.cost_total,
                    "from_primary": L.from_primary,
                    "from_reserve": L.from_reserve,
                    "pnl": L.pnl,
                    "ret": (L.pnl / L.cost_total) if L.pnl is not None and L.cost_total else None,
                    "extended_days": L.extended_days,
                    "exit_reason": L.exit_reason,
                    "closed": L.closed,
                }
            )
        return pd.DataFrame(rows)

    ld = lots_df(sim_rule)
    ld.to_csv(OUT / "lots_hold_to_profit.csv", index=False)
    pd.DataFrame(sim_rule.events).to_csv(OUT / "events_hold_to_profit.csv", index=False)
    lots_df(sim_base).to_csv(OUT / "lots_cash_baseline.csv", index=False)

    # Equity curves
    eq_df = pd.DataFrame(
        {
            "trade_date": dates[m].values,
            "bh_60k": eq_bh,
            "flex_agg_60k_pct": eq_a,
            "flex_agg_30k_risk_30k_idle": eq_b,
            "cash_baseline_force": sim_base.equity,
            "hold_to_profit": sim_rule.equity,
        }
    )
    eq_df.to_csv(OUT / "equity_curves.csv", index=False)

    rows = [
        {
            "tag": "BH_CSI300_60k",
            **{k: st_bh.get(k) for k in ["total_return", "ann_return", "max_dd", "sharpe", "final_equity"]},
            "exposure": 1.0,
            "trade_count": 0,
            "win_rate": float("nan"),
        },
        {
            "tag": "FLEX_AGG_60k_pct",
            **{k: st_a.get(k) for k in ["total_return", "ann_return", "max_dd", "sharpe", "final_equity", "exposure", "trade_count", "win_rate"]},
        },
        {
            "tag": "FLEX_AGG_30k_risk_30k_idle",
            **{k: st_b.get(k) for k in ["total_return", "ann_return", "max_dd", "sharpe", "final_equity", "exposure", "trade_count", "win_rate"]},
        },
        {
            "tag": "CASH_BASELINE_FORCE_EXIT",
            **sim_base.stats,
            **{f"x_{k}": v for k, v in sim_base.extra.items()},
        },
        {
            "tag": "HOLD_TO_PROFIT_PYRAMID",
            **sim_rule.stats,
            **{f"x_{k}": v for k, v in sim_rule.extra.items()},
        },
    ]
    # flatten only main metrics table
    main_tbl = pd.DataFrame(
        [
            {
                "策略": "买入持有 CSI300（6万）",
                "总收益": st_bh["total_return"],
                "年化": st_bh["ann_return"],
                "最大回撤": st_bh["max_dd"],
                "Sharpe": st_bh["sharpe"],
                "期末资金": st_bh["final_equity"],
                "暴露": 1.0,
            },
            {
                "策略": "原版进取 %路径（6万全跟）",
                "总收益": st_a["total_return"],
                "年化": st_a["ann_return"],
                "最大回撤": st_a["max_dd"],
                "Sharpe": st_a["sharpe"],
                "期末资金": st_a["final_equity"],
                "暴露": st_a.get("exposure"),
            },
            {
                "策略": "原版进取（仅3万风险+3万闲置）",
                "总收益": st_b["total_return"],
                "年化": st_b["ann_return"],
                "最大回撤": st_b["max_dd"],
                "Sharpe": st_b["sharpe"],
                "期末资金": st_b["final_equity"],
                "暴露": st_b.get("exposure"),
            },
            {
                "策略": "现金账本·到期必平·不补仓（对照）",
                "总收益": sim_base.stats["total_return"],
                "年化": sim_base.stats["ann_return"],
                "最大回撤": sim_base.stats["max_dd"],
                "Sharpe": sim_base.stats["sharpe"],
                "期末资金": sim_base.stats["final_equity"],
                "暴露": sim_base.stats["exposure"],
            },
            {
                "策略": "【本规则】亏延至盈 + 3万补仓可新开",
                "总收益": sim_rule.stats["total_return"],
                "年化": sim_rule.stats["ann_return"],
                "最大回撤": sim_rule.stats["max_dd"],
                "Sharpe": sim_rule.stats["sharpe"],
                "期末资金": sim_rule.stats["final_equity"],
                "暴露": sim_rule.stats["exposure"],
            },
        ]
    )
    main_tbl.to_csv(OUT / "metrics_comparison.csv", index=False)

    summary = {
        "window": {"start": str(START.date()), "end": str(END.date()), "n": int(m.sum())},
        "capital": {"primary": PRIMARY0, "reserve": RESERVE0, "total": TOTAL0},
        "cost_bps": 3,
        "rules": {
            "hold_to_profit": True,
            "allow_new_open_with_reserve": True,
            "no_max_hold": True,
            "profit_vs": "lot cost_total including buy cost; sell at open*(1-cost)",
        },
        "A_flex_60k": st_a,
        "B_flex_30k_risk": st_b,
        "C_cash_baseline": {"stats": sim_base.stats, "extra": sim_base.extra},
        "D_hold_to_profit": {"stats": sim_rule.stats, "extra": sim_rule.extra},
        "E_bh": st_bh,
        "extension_audit": audit,
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # Report
    ex = sim_rule.extra
    lines = [
        "# Flex 进取 + 亏损延期至盈利 + 3万/3万补仓（可新开）严格测算",
        "",
        f"- 窗口：**{START.date()} → {END.date()}**（{int(m.sum())} 交易日）",
        f"- 资金：首仓 **{PRIMARY0:,.0f}** + 补仓 **{RESERVE0:,.0f}** = **{TOTAL0:,.0f}**",
        "- 成本：3bp 单边；盈利判定=开盘卖出价净额 > 该 lot 成本（含买费）",
        "- 入场：Flex 进取（核心 60≤RT<80 & dd≤-5% H5；卫星 stage；单仓满 60/40）",
        "- 出场：计划持有日到期若仍亏 → 延期至盈利再卖；无最长持有",
        "- 补仓：持仓期间新信号可用补仓池 **按权重新开/加仓**",
        "",
        "## 主结果对比",
        "",
        "| 策略 | 总收益 | 年化 | 最大回撤 | Sharpe | 期末(元) | 暴露 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in main_tbl.iterrows():
        lines.append(
            f"| {r['策略']} | {pct(r['总收益'])} | {pct(r['年化'])} | {pct(r['最大回撤'])} | "
            f"{num(r['Sharpe'])} | {num(r['期末资金'], 0)} | {pct(r['暴露'])} |"
        )

    lines += [
        "",
        "## 本规则交易结构",
        "",
        f"- 开仓 lot 数：{ex['n_lots']}",
        f"- 已平仓：{ex['n_closed']} · 胜率 {pct(sim_rule.stats.get('win_rate'))}",
        f"- 发生过延期的 lot：{ex['n_extended']} · 平均延期 {num(ex['avg_extended_days'], 1)} 日 · 最长延期 {ex['max_extended_days']} 日",
        f"- 样本末强制结算：{ex['n_eod_force']} 笔 · 强制结算盈亏合计 {num(ex['eod_force_pnl'], 0)} 元",
        f"- 补仓池买入次数（from_reserve>0）：{ex['pyramid_buys']}",
        f"- 首仓池买入次数：{ex['primary_buys']}",
        f"- 已实现盈亏合计（窗内平仓）：{num(ex['sum_pnl_closed'], 0)} 元",
        f"- 期末现金 首/补：{num(ex['final_cash_p'], 0)} / {num(ex['final_cash_r'], 0)}",
        "",
        "## 相对解读",
        "",
        "1. **公平对照**应看「现金账本·到期必平」与「本规则」——同一套成交与资金池口径。",
        "2. 「原版进取 %路径 6万全跟」暴露更高、复利路径不同，数字会显著更大，**不宜直接当同一资金纪律**。",
        "3. 「仅3万风险+3万闲置」更接近「首仓3万、补仓永远不用」的上限参照。",
        "4. 亏损延期至盈利会：**抬升胜率、拉长持有、在单边下跌中放大回撤与资金占用**；本窗结果见上表。",
        "",
        "## 文件",
        "",
        "- `metrics_comparison.csv`",
        "- `lots_hold_to_profit.csv` / `events_hold_to_profit.csv`",
        "- `equity_curves.csv` / `summary.json`",
        "",
        "研究回测，非投资建议。",
        "",
    ]
    report = "\n".join(lines)
    (OUT / "report.md").write_text(report, encoding="utf-8")
    print(report, flush=True)
    print(f"Wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
