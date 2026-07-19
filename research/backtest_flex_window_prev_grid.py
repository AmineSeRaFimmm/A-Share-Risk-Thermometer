#!/usr/bin/env python3
"""Re-run the *previously successful* dense Flex grid on 2025-05 → 2026-07-13 only.

Grid sizes match research/backtest_flex_dense_grid.py (the run that finished):
  - core ~43k, sat ~536, portfolio ~486k
Metrics / ranking use only the window; features still use full history for warmup.
"""
from __future__ import annotations

import itertools
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.backtest_core_plus_sectors import load_aligned  # noqa: E402
from research.backtest_flex_dense_grid import (  # noqa: E402
    combine_port,
    extend_aligned_to_end,
    precompute_stages,
    score_row,
    simulate_core_daily,
    simulate_sat_daily,
    stats_from_daily,
)

OUT = ROOT / "research/output/flex_window_202505_20260713"
START = pd.Timestamp("2025-05-01")
END = pd.Timestamp("2026-07-13")
EXT_END = pd.Timestamp("2026-07-14")


def window_daily(daily: np.ndarray, dates: pd.Series) -> np.ndarray:
    d = np.asarray(daily, dtype=float).copy()
    m = (dates.values >= np.datetime64(START)) & (dates.values <= np.datetime64(END))
    return d[m]


def win_stats(daily_full: np.ndarray, dates: pd.Series) -> dict:
    return stats_from_daily(window_daily(daily_full, dates))


def is_cut(dates: pd.Series, frac: float = 0.60) -> pd.Timestamp:
    m = (dates.values >= np.datetime64(START)) & (dates.values <= np.datetime64(END))
    idx = np.where(m)[0]
    cut = idx[int(len(idx) * frac)]
    return pd.Timestamp(dates.iloc[cut])


def is_oos_stats(daily_full: np.ndarray, dates: pd.Series, cut: pd.Timestamp) -> dict:
    d = np.asarray(daily_full, dtype=float)
    full = win_stats(d, dates)
    is_mask = (dates.values >= np.datetime64(START)) & (dates.values < np.datetime64(cut))
    oos_mask = (dates.values >= np.datetime64(cut)) & (dates.values <= np.datetime64(END))
    is_part = stats_from_daily(d[is_mask])
    oos_part = stats_from_daily(d[oos_mask])
    return {
        **{f"full_{k}": v for k, v in full.items()},
        **{f"is_{k}": v for k, v in is_part.items()},
        **{f"oos_{k}": v for k, v in oos_part.items()},
        "score": score_row(
            {
                "ann_return": full["ann_return"],
                "oos_ann_return": oos_part["ann_return"],
                "max_dd": full["max_dd"],
                "trade_count": max(int(full.get("trade_count") or 0), 8),
            }
        ),
    }


def pct(x) -> str:
    try:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return "—"
        return f"{float(x):.2%}"
    except Exception:
        return "—"


def main() -> None:
    warnings.filterwarnings("ignore")
    OUT.mkdir(parents=True, exist_ok=True)
    t_all = time.time()

    print("=== Load + extend (same as successful dense grid) ===", flush=True)
    df0, meta0 = load_aligned()
    df, meta, audit = extend_aligned_to_end(df0, meta0, EXT_END)
    df = df[df["trade_date"] <= END].reset_index(drop=True)
    dates = df["trade_date"]
    win_n = int(((dates >= START) & (dates <= END)).sum())
    cut = is_cut(dates, 0.60)
    print(
        f"rows={len(df)} window_n={win_n} {dates[(dates>=START)&(dates<=END)].min().date()}→"
        f"{dates[(dates>=START)&(dates<=END)].max().date()} IS_cut={cut.date()}",
        flush=True,
    )
    (OUT / "data_extension_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    rt = df["rt"].to_numpy(dtype=float)
    dd = df["dd60"].to_numpy(dtype=float)
    csi_open = df["csi_open"].to_numpy(dtype=float)
    csi_close = df["csi_close"].to_numpy(dtype=float)

    bh = df["csi_close"].pct_change().fillna(0.0).to_numpy(dtype=float)
    bh_s = win_stats(bh, dates)
    print(f"B&H window ann={bh_s['ann_return']:.2%} total={bh_s['total_return']:.2%} dd={bh_s['max_dd']:.2%}", flush=True)

    # ========== A) same core grid as successful run ==========
    print("=== A) Core grid (same as successful dense) ===", flush=True)
    rt_lows = list(range(50, 71))
    rt_highs = list(range(70, 91))
    dd_maxes = [-0.03, -0.04, -0.05, -0.06, -0.07, -0.08, -0.10]
    core_holds = [3, 4, 5, 6, 7, 8, 10]
    cost_bps_core = [3, 15]

    core_rows = []
    t0 = time.time()
    for cost_bps in cost_bps_core:
        bc = sc = cost_bps / 10000.0
        for rt_low, rt_high, dd_max, hold in itertools.product(rt_lows, rt_highs, dd_maxes, core_holds):
            if rt_low >= rt_high:
                continue
            daily, _ = simulate_core_daily(
                rt, dd, csi_open, csi_close,
                rt_low=rt_low, rt_high=rt_high, dd_max=dd_max, hold_days=hold,
                buy_cost=bc, sell_cost=sc,
            )
            st = is_oos_stats(daily, dates, cut)
            core_rows.append(
                {
                    "rt_low": rt_low,
                    "rt_high": rt_high,
                    "dd_max": dd_max,
                    "core_hold": hold,
                    "cost_bps": cost_bps,
                    **st,
                }
            )
    core_df = pd.DataFrame(core_rows).sort_values("score", ascending=False)
    core_df.to_csv(OUT / "grid_core.csv", index=False)
    print(f"core={len(core_df)} in {time.time()-t0:.1f}s", flush=True)

    prod_core = (60, 80, -0.05, 5)
    top_core = [prod_core]
    for _, r in core_df[core_df.cost_bps == 3].head(15).iterrows():
        k = (int(r.rt_low), int(r.rt_high), float(r.dd_max), int(r.core_hold))
        if k not in top_core:
            top_core.append(k)
        if len(top_core) >= 12:
            break

    # ========== B) same sat grid ==========
    print("=== B) Sat hold grid (same as successful dense) ===", flush=True)
    stages_all = precompute_stages(df)
    sat_mins = [2, 3, 4]
    sat_defaults = [3, 4, 5, 6, 7, 8]
    sat_maxes = [5, 6, 7, 8, 10]
    event_flags = [True, False]
    hair_flags = [True, False]
    cost_bps_sat = [3, 15]

    sat_cache: dict[tuple, np.ndarray] = {}
    sat_rows = []
    t0 = time.time()
    for cost_bps, smin, sdef, smax, ev, hair in itertools.product(
        cost_bps_sat, sat_mins, sat_defaults, sat_maxes, event_flags, hair_flags
    ):
        if not (smin <= sdef <= smax):
            continue
        key = (cost_bps, smin, sdef, smax, ev, hair)
        daily, _ = simulate_sat_daily(
            df, meta, stages_all,
            sat_min=smin, sat_default=sdef, sat_max=smax,
            buy_cost=cost_bps / 10000.0, sell_cost=cost_bps / 10000.0,
            apply_haircut=hair, event_exit=ev,
        )
        sat_cache[key] = daily
        st = is_oos_stats(daily, dates, cut)
        sat_rows.append(
            {
                "cost_bps": cost_bps,
                "sat_min": smin,
                "sat_default": sdef,
                "sat_max": smax,
                "event_exit": ev,
                "haircut": hair,
                **st,
            }
        )
    sat_df = pd.DataFrame(sat_rows).sort_values("score", ascending=False)
    sat_df.to_csv(OUT / "grid_sat.csv", index=False)
    print(f"sat={len(sat_df)} in {time.time()-t0:.1f}s", flush=True)

    prod_sat_base = (3, 5, 8, True, True)
    sat_bases = [prod_sat_base]
    for _, r in sat_df[sat_df.cost_bps == 3].head(8).iterrows():
        b = (int(r.sat_min), int(r.sat_default), int(r.sat_max), bool(r.event_exit), bool(r.haircut))
        if b not in sat_bases:
            sat_bases.append(b)
        if len(sat_bases) >= 6:
            break

    # ========== C) same portfolio grid structure ==========
    print("=== C) Portfolio sizing grid (same structure) ===", flush=True)
    w_cores = [i / 100 for i in range(30, 101, 5)]
    w_sats = [i / 100 for i in range(0, 71, 5)]
    total_caps = [0.60, 0.70, 0.80, 0.90, 1.00]
    single_fulls = [False, True]
    cost_bps_port = [3, 15, 30]

    core_daily_cache: dict[tuple, np.ndarray] = {}
    for cost_bps in cost_bps_port:
        bc = sc = cost_bps / 10000.0
        for rt_low, rt_high, dd_max, hold in top_core:
            key = (cost_bps, rt_low, rt_high, dd_max, hold)
            daily, _ = simulate_core_daily(
                rt, dd, csi_open, csi_close,
                rt_low=rt_low, rt_high=rt_high, dd_max=dd_max, hold_days=hold,
                buy_cost=bc, sell_cost=sc,
            )
            core_daily_cache[key] = daily

    for cost_bps in cost_bps_port:
        for base in sat_bases:
            key = (cost_bps, *base)
            if key not in sat_cache:
                sat_cache[key] = simulate_sat_daily(
                    df, meta, stages_all,
                    sat_min=base[0], sat_default=base[1], sat_max=base[2],
                    buy_cost=cost_bps / 10000.0, sell_cost=cost_bps / 10000.0,
                    apply_haircut=base[4], event_exit=base[3],
                )[0]

    port_rows = []
    t0 = time.time()
    for cost_bps in cost_bps_port:
        for rt_low, rt_high, dd_max, hold in top_core:
            c_daily = core_daily_cache[(cost_bps, rt_low, rt_high, dd_max, hold)]
            for base in sat_bases:
                s_daily = sat_cache[(cost_bps, *base)]
                for w_c, w_s, cap, single in itertools.product(w_cores, w_sats, total_caps, single_fulls):
                    if w_c + w_s <= 0:
                        continue
                    port = combine_port(
                        c_daily, s_daily,
                        w_core=w_c, w_sat=w_s, total_cap=cap, flex_single_full=single,
                    )
                    st = is_oos_stats(port, dates, cut)
                    tag = ""
                    if (
                        cost_bps == 3
                        and (rt_low, rt_high, dd_max, hold) == prod_core
                        and base == prod_sat_base
                    ):
                        if abs(w_c - 0.5) < 1e-9 and abs(w_s - 0.3) < 1e-9 and abs(cap - 0.8) < 1e-9 and not single:
                            tag = "PROD_CONSERVATIVE"
                        if abs(w_c - 0.6) < 1e-9 and abs(w_s - 0.4) < 1e-9 and abs(cap - 1.0) < 1e-9 and single:
                            tag = "PROD_AGGRESSIVE"
                    port_rows.append(
                        {
                            "cost_bps": cost_bps,
                            "rt_low": rt_low,
                            "rt_high": rt_high,
                            "dd_max": dd_max,
                            "core_hold": hold,
                            "sat_min": base[0],
                            "sat_default": base[1],
                            "sat_max": base[2],
                            "event_exit": base[3],
                            "haircut": base[4],
                            "w_core": w_c,
                            "w_sat": w_s,
                            "total_cap": cap,
                            "flex_single_full": single,
                            "tag": tag,
                            **st,
                        }
                    )
    port_df = pd.DataFrame(port_rows).sort_values("score", ascending=False)
    port_df.to_csv(OUT / "grid_portfolio.csv", index=False)
    print(f"portfolio={len(port_df)} in {time.time()-t0:.1f}s", flush=True)

    dual = port_df[(port_df.cost_bps == 3) & (port_df.w_core >= 0.2) & (port_df.w_sat >= 0.15)].sort_values(
        "score", ascending=False
    )

    def tag_row(tag: str):
        s = port_df[port_df.tag == tag]
        return None if s.empty else s.iloc[0].to_dict()

    cons, agg = tag_row("PROD_CONSERVATIVE"), tag_row("PROD_AGGRESSIVE")
    best = port_df.iloc[0].to_dict()
    best_dual = dual.iloc[0].to_dict() if len(dual) else best
    c3 = core_df[core_df.cost_bps == 3].reset_index(drop=True)
    mprod = c3[(c3.rt_low == 60) & (c3.rt_high == 80) & np.isclose(c3.dd_max, -0.05) & (c3.core_hold == 5)]
    prod_rank = int(mprod.index[0]) + 1 if len(mprod) else None

    summary = {
        "window": {"start": str(START.date()), "end": str(END.date()), "n": int(((dates >= START) & (dates <= END)).sum())},
        "is_cut": str(cut.date()),
        "grid": "same as successful flex_dense_grid (core~43k sat~536 port~486k structure)",
        "grid_sizes": {"core": len(core_df), "sat": len(sat_df), "portfolio": len(port_df)},
        "bh": bh_s,
        "production_conservative": cons,
        "production_aggressive": agg,
        "best": best,
        "best_dual": best_dual,
        "prod_core_rank_3bps": prod_rank,
        "prod_core_n_3bps": int(len(c3)),
        "extension_audit": audit,
        "elapsed_sec": time.time() - t_all,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# Flex 网格回测（沿用成功网格）{START.date()} → {END.date()}",
        "",
        f"- 窗口交易日 **{summary['window']['n']}**（{dates[(dates>=START)&(dates<=END)].min().date()} → {dates[(dates>=START)&(dates<=END)].max().date()}）",
        f"- IS/OOS 切分：**{cut.date()}**（前60% / 后40%）",
        f"- 网格与上次成功跑法一致：核心 **{len(core_df)}** · 卫星 **{len(sat_df)}** · 组合 **{len(port_df)}**",
        f"- 官方 RT 截止 `{audit.get('official_end')}`；填充 {len(audit.get('filled_days') or [])} 天",
        f"- CSI300 窗口买入持有：年化 {pct(bh_s['ann_return'])} · 总收益 {pct(bh_s['total_return'])} · 回撤 {pct(bh_s['max_dd'])}",
        f"- 耗时 {summary['elapsed_sec']:.1f}s",
        "",
        "## 生产参数（本窗口）",
        "",
        "| 模式 | 年化 | 总收益 | 最大回撤 | Sharpe | IS年化 | OOS年化 | OOS回撤 | 暴露 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def md(name, r):
        if not r:
            return f"| {name} | — |"
        return (
            f"| {name} | {pct(r.get('full_ann_return'))} | {pct(r.get('full_total_return'))} | "
            f"{pct(r.get('full_max_dd'))} | {float(r.get('full_sharpe') or 0):.2f} | "
            f"{pct(r.get('is_ann_return'))} | {pct(r.get('oos_ann_return'))} | {pct(r.get('oos_max_dd'))} | "
            f"{pct(r.get('full_exposure_ratio'))} |"
        )

    lines += [
        md("保守 50/30 cap80%", cons),
        md("进取 60/40 单仓满", agg),
        md("网格最优", best),
        md("双仓最优 w_sat≥15%", best_dual),
        "",
        f"生产核心 60–80/-5%/持5 在本窗口 3bp 核心网格排名：**#{prod_rank} / {len(c3)}**" if prod_rank else "",
        "",
        "## 组合 Top 12",
        "",
        "| # | score | 核心 | hold | 仓位 | cap | single | sat | cost | 年化 | 回撤 | OOS年化 |",
        "|---:|---:|---|---:|---|---:|---|---|---:|---:|---:|---:|",
    ]
    for i, (_, r) in enumerate(port_df.head(12).iterrows(), 1):
        lines.append(
            f"| {i} | {r.score:.4f} | [{int(r.rt_low)},{int(r.rt_high)})≤{float(r.dd_max):.0%} | {int(r.core_hold)} | "
            f"{float(r.w_core):.0%}/{float(r.w_sat):.0%} | {float(r.total_cap):.0%} | {bool(r.flex_single_full)} | "
            f"{int(r.sat_min)}-{int(r.sat_default)}-{int(r.sat_max)} | {int(r.cost_bps)} | "
            f"{pct(r.full_ann_return)} | {pct(r.full_max_dd)} | {pct(r.oos_ann_return)} |"
        )

    lines += [
        "",
        "## 双仓 Top 10（3bp, w_sat≥15%）",
        "",
        "| # | score | 核心 | hold | 仓位 | cap | single | sat | 年化 | 回撤 | OOS年化 |",
        "|---:|---:|---|---:|---|---:|---|---|---:|---:|---:|",
    ]
    for i, (_, r) in enumerate(dual.head(10).iterrows(), 1):
        lines.append(
            f"| {i} | {r.score:.4f} | [{int(r.rt_low)},{int(r.rt_high)})≤{float(r.dd_max):.0%} | {int(r.core_hold)} | "
            f"{float(r.w_core):.0%}/{float(r.w_sat):.0%} | {float(r.total_cap):.0%} | {bool(r.flex_single_full)} | "
            f"{int(r.sat_min)}-{int(r.sat_default)}-{int(r.sat_max)} | "
            f"{pct(r.full_ann_return)} | {pct(r.full_max_dd)} | {pct(r.oos_ann_return)} |"
        )

    lines += [
        "",
        "## 核心 Top 10（3bp）",
        "",
        "| # | score | rt_low | rt_high | dd | hold | 年化 | 回撤 | OOS年化 | 暴露 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, (_, r) in enumerate(c3.head(10).iterrows(), 1):
        lines.append(
            f"| {i} | {r.score:.4f} | {int(r.rt_low)} | {int(r.rt_high)} | {float(r.dd_max):.0%} | {int(r.core_hold)} | "
            f"{pct(r.full_ann_return)} | {pct(r.full_max_dd)} | {pct(r.oos_ann_return)} | {pct(r.full_exposure_ratio)} |"
        )

    lines += [
        "",
        "## 说明",
        "",
        "1. **网格参数与上次成功的 `backtest_flex_dense_grid.py` 相同**，仅把评价窗口改为 2025-05～2026-07-13。",
        "2. RT/回撤特征用全历史计算；收益只计窗口内。",
        "3. 价格实际到 2026-07-13；7/14 无完整指数 bar。",
        "4. 研究回测，非投资建议。",
        "",
    ]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("Wrote", OUT / "report.md", flush=True)
    print("=== DONE ===", f"{time.time()-t_all:.1f}s", flush=True)


if __name__ == "__main__":
    main()
