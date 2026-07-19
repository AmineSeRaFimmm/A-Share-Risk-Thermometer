#!/usr/bin/env python3
"""Ultra-dense Flex grid backtest for 2025-05 → 2026-07-13.

Reuses engine helpers from backtest_flex_dense_grid.py.
- Warmup: load full history for RT/dd features, then evaluate only inside the window.
- RT gaps after official end filled with nowcast (see extension audit).
- Within-window split: IS = first 60% trading days, OOS = last 40%.

Outputs: research/output/flex_window_202505_20260713/
"""
from __future__ import annotations

import itertools
import json
import math
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.backtest_flex_dense_grid import (  # noqa: E402
    END_DATE,
    combine_port,
    extend_aligned_to_end,
    oos_daily,
    precompute_stages,
    score_row,
    simulate_core_daily,
    simulate_sat_daily,
    stats_from_daily,
)
from research.backtest_core_plus_sectors import load_aligned  # noqa: E402

OUT = ROOT / "research/output/flex_window_202505_20260713"
START = pd.Timestamp("2025-05-01")
END = pd.Timestamp("2026-07-13")
# extend target still allows nowcast bridge up to END
EXT_END = pd.Timestamp("2026-07-14")


def slice_window(daily: np.ndarray, dates: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    out = np.asarray(daily, dtype=float).copy()
    mask = (dates.values >= np.datetime64(start)) & (dates.values <= np.datetime64(end))
    out[~mask] = 0.0
    return out


def window_stats(daily_full: np.ndarray, dates: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    d = slice_window(daily_full, dates, start, end)
    # only count days inside window for ann/exposure
    mask = (dates.values >= np.datetime64(start)) & (dates.values <= np.datetime64(end))
    d_in = d[mask]
    return stats_from_daily(d_in)


def is_oos_split(dates: pd.Series, start: pd.Timestamp, end: pd.Timestamp, is_frac: float = 0.60):
    mask = (dates.values >= np.datetime64(start)) & (dates.values <= np.datetime64(end))
    idx = np.where(mask)[0]
    if len(idx) < 20:
        cut = idx[len(idx) // 2] if len(idx) else 0
    else:
        cut = idx[int(len(idx) * is_frac)]
    is_end = pd.Timestamp(dates.iloc[cut])
    return is_end


def stats_is_oos(daily_full: np.ndarray, dates: pd.Series, start: pd.Timestamp, end: pd.Timestamp, is_end: pd.Timestamp) -> dict:
    full = window_stats(daily_full, dates, start, end)
    is_part = window_stats(daily_full, dates, start, is_end - pd.Timedelta(days=1))
    oos_part = window_stats(daily_full, dates, is_end, end)
    return {
        **{f"full_{k}": v for k, v in full.items()},
        **{f"is_{k}": v for k, v in is_part.items()},
        **{f"oos_{k}": v for k, v in oos_part.items()},
        "score": score_row(
            {
                "ann_return": full["ann_return"],
                "oos_ann_return": oos_part["ann_return"],
                "max_dd": full["max_dd"],
                "trade_count": max(int(full["trade_count"] or 0), 5),
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

    print("=== Load + extend ===")
    df0, meta0 = load_aligned()
    df, meta, audit = extend_aligned_to_end(df0, meta0, EXT_END)
    # clamp to END (price end)
    df = df[df["trade_date"] <= END].reset_index(drop=True)
    dates = df["trade_date"]
    win_mask = (dates >= START) & (dates <= END)
    print(
        f"full_rows={len(df)} window_n={int(win_mask.sum())} "
        f"{dates[win_mask].min().date()} → {dates[win_mask].max().date()}"
    )
    (OUT / "data_extension_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    rt = df["rt"].to_numpy(dtype=float)
    dd = df["dd60"].to_numpy(dtype=float)
    csi_open = df["csi_open"].to_numpy(dtype=float)
    csi_close = df["csi_close"].to_numpy(dtype=float)
    n = len(df)
    is_end = is_oos_split(dates, START, END, 0.60)
    print(f"IS ends before {is_end.date()} | OOS {is_end.date()} → {END.date()}")

    # BH in window
    bh = df["csi_close"].pct_change().fillna(0.0).to_numpy(dtype=float)
    bh_win = window_stats(bh, dates, START, END)
    print(f"B&H window ann={bh_win['ann_return']:.2%} dd={bh_win['max_dd']:.2%} total={bh_win['total_return']:.2%}")

    # ------------------------------------------------------------------
    # A) Ultra-dense CORE grid
    # ------------------------------------------------------------------
    print("=== A) Core dense grid ===")
    # Dense around production neighborhood (still wider than prod)
    rt_lows = list(range(52, 68))  # 16
    rt_highs = list(range(72, 88))  # 16
    dd_maxes = [round(x, 3) for x in np.arange(-0.10, -0.022, 0.005)]  # 0.5% steps
    core_holds = list(range(3, 11))  # 3..10
    # Full mesh at 3bp; re-price top + production at stress costs
    core_rows = []
    t0 = time.time()
    daily_cache_3bp: dict[tuple, np.ndarray] = {}
    for rt_low, rt_high, dd_max, hold in itertools.product(rt_lows, rt_highs, dd_maxes, core_holds):
        if rt_low >= rt_high:
            continue
        daily, _rets = simulate_core_daily(
            rt,
            dd,
            csi_open,
            csi_close,
            rt_low=rt_low,
            rt_high=rt_high,
            dd_max=dd_max,
            hold_days=hold,
            buy_cost=0.0003,
            sell_cost=0.0003,
        )
        key = (rt_low, rt_high, dd_max, hold)
        daily_cache_3bp[key] = daily
        st = stats_is_oos(daily, dates, START, END, is_end)
        core_rows.append({"rt_low": rt_low, "rt_high": rt_high, "dd_max": dd_max, "core_hold": hold, "cost_bps": 3, **st})

    core_df = pd.DataFrame(core_rows).sort_values("score", ascending=False)
    # stress re-eval top 80 + production at 15/30 bps
    stress_keys = [(60, 80, -0.05, 5)]
    for _, r in core_df.head(80).iterrows():
        k = (int(r.rt_low), int(r.rt_high), float(r.dd_max), int(r.core_hold))
        if k not in stress_keys:
            stress_keys.append(k)
    for cost_bps in (15, 30):
        bc = sc = cost_bps / 10000.0
        for rt_low, rt_high, dd_max, hold in stress_keys:
            daily, _ = simulate_core_daily(
                rt,
                dd,
                csi_open,
                csi_close,
                rt_low=rt_low,
                rt_high=rt_high,
                dd_max=dd_max,
                hold_days=hold,
                buy_cost=bc,
                sell_cost=sc,
            )
            st = stats_is_oos(daily, dates, START, END, is_end)
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
    print(f"core combos={len(core_df)} in {time.time() - t0:.1f}s")

    # selected cores for port: production + top by score at 3bp with enough exposure
    c3 = core_df[(core_df.cost_bps == 3) & (core_df.full_exposure_ratio > 0.05)].copy()
    top_core_params = [(60, 80, -0.05, 5)]
    for _, r in c3.head(20).iterrows():
        key = (int(r.rt_low), int(r.rt_high), float(r.dd_max), int(r.core_hold))
        if key not in top_core_params:
            top_core_params.append(key)
        if len(top_core_params) >= 12:
            break

    # ------------------------------------------------------------------
    # B) Ultra-dense SAT hold grid
    # ------------------------------------------------------------------
    print("=== B) Sat hold dense grid ===")
    stages_all = precompute_stages(df)
    sat_mins = [2, 3, 4, 5]
    sat_defaults = list(range(3, 10))
    sat_maxes = list(range(5, 12))
    event_flags = [True, False]
    hair_flags = [True, False]
    cost_bps_sat = [3, 15, 30]

    sat_cache: dict[tuple, np.ndarray] = {}
    sat_rows = []
    t0 = time.time()
    for cost_bps, smin, sdef, smax, ev, hair in itertools.product(
        cost_bps_sat, sat_mins, sat_defaults, sat_maxes, event_flags, hair_flags
    ):
        if not (smin <= sdef <= smax):
            continue
        key = (cost_bps, smin, sdef, smax, ev, hair)
        daily, rets = simulate_sat_daily(
            df,
            meta,
            stages_all,
            sat_min=smin,
            sat_default=sdef,
            sat_max=smax,
            buy_cost=cost_bps / 10000.0,
            sell_cost=cost_bps / 10000.0,
            apply_haircut=hair,
            event_exit=ev,
        )
        sat_cache[key] = daily
        st = stats_is_oos(daily, dates, START, END, is_end)
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
    print(f"sat combos={len(sat_df)} in {time.time() - t0:.1f}s")

    prod_sat_base = (3, 5, 8, True, True)  # min, default, max, event, hair
    sat_bases = [prod_sat_base]
    for _, r in sat_df[sat_df.cost_bps == 3].head(12).iterrows():
        b = (int(r.sat_min), int(r.sat_default), int(r.sat_max), bool(r.event_exit), bool(r.haircut))
        if b not in sat_bases:
            sat_bases.append(b)
        if len(sat_bases) >= 8:
            break

    # ------------------------------------------------------------------
    # C) Portfolio sizing — denser steps (2%)
    # ------------------------------------------------------------------
    print("=== C) Portfolio sizing grid ===")
    # 2% on a reduced (w_c,w_s) band near production + 5% elsewhere for density without 10M combos
    w_cores = sorted(set([i / 100 for i in range(30, 101, 5)] + [i / 100 for i in range(40, 81, 2)]))
    w_sats = sorted(set([i / 100 for i in range(0, 71, 5)] + [i / 100 for i in range(10, 51, 2)]))
    total_caps = [0.60, 0.70, 0.80, 0.90, 1.00]
    single_fulls = [False, True]
    cost_bps_port = [3, 15, 30]

    core_daily_cache: dict[tuple, np.ndarray] = {}
    for cost_bps in cost_bps_port:
        bc = sc = cost_bps / 10000.0
        for rt_low, rt_high, dd_max, hold in top_core_params:
            key = (cost_bps, rt_low, rt_high, dd_max, hold)
            daily, _ = simulate_core_daily(
                rt,
                dd,
                csi_open,
                csi_close,
                rt_low=rt_low,
                rt_high=rt_high,
                dd_max=dd_max,
                hold_days=hold,
                buy_cost=bc,
                sell_cost=sc,
            )
            core_daily_cache[key] = daily

    for cost_bps in cost_bps_port:
        for base in sat_bases:
            key = (cost_bps, *base)
            if key not in sat_cache:
                sat_cache[key] = simulate_sat_daily(
                    df,
                    meta,
                    stages_all,
                    sat_min=base[0],
                    sat_default=base[1],
                    sat_max=base[2],
                    buy_cost=cost_bps / 10000.0,
                    sell_cost=cost_bps / 10000.0,
                    apply_haircut=base[4],
                    event_exit=base[3],
                )[0]

    port_rows = []
    t0 = time.time()
    for cost_bps in cost_bps_port:
        for rt_low, rt_high, dd_max, hold in top_core_params:
            c_daily = core_daily_cache[(cost_bps, rt_low, rt_high, dd_max, hold)]
            for base in sat_bases:
                s_daily = sat_cache[(cost_bps, *base)]
                for w_c, w_s, cap, single in itertools.product(w_cores, w_sats, total_caps, single_fulls):
                    if w_c + w_s <= 0:
                        continue
                    port = combine_port(
                        c_daily,
                        s_daily,
                        w_core=w_c,
                        w_sat=w_s,
                        total_cap=cap,
                        flex_single_full=single,
                    )
                    st = stats_is_oos(port, dates, START, END, is_end)
                    tag = ""
                    if (
                        cost_bps == 3
                        and (rt_low, rt_high, dd_max, hold) == (60, 80, -0.05, 5)
                        and base == prod_sat_base
                    ):
                        if abs(w_c - 0.5) < 1e-9 and abs(w_s - 0.3) < 1e-9 and abs(cap - 0.8) < 1e-9 and single is False:
                            tag = "PROD_CONSERVATIVE"
                        if abs(w_c - 0.6) < 1e-9 and abs(w_s - 0.4) < 1e-9 and abs(cap - 1.0) < 1e-9 and single is True:
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
    print(f"portfolio combos={len(port_df)} in {time.time() - t0:.1f}s")

    # dual-sleeve filter tops
    dual = port_df[(port_df.w_sat >= 0.15) & (port_df.w_core >= 0.20) & (port_df.cost_bps == 3)].copy()
    dual = dual.sort_values("score", ascending=False)

    def get_tag(tag: str):
        sub = port_df[port_df.tag == tag]
        return None if sub.empty else sub.iloc[0].to_dict()

    cons = get_tag("PROD_CONSERVATIVE")
    agg = get_tag("PROD_AGGRESSIVE")
    best = port_df.iloc[0].to_dict()
    best_dual = dual.iloc[0].to_dict() if len(dual) else best

    summary = {
        "window": {"start": str(START.date()), "end": str(END.date())},
        "is_oos_cut": str(is_end.date()),
        "window_n": int(win_mask.sum()),
        "full_n": int(n),
        "extension_audit": audit,
        "bh_window": bh_win,
        "grid_sizes": {
            "core": int(len(core_df)),
            "sat": int(len(sat_df)),
            "portfolio": int(len(port_df)),
        },
        "production_conservative": cons,
        "production_aggressive": agg,
        "best_overall": best,
        "best_dual_sleeve_w_sat_ge_15": best_dual,
        "top20_portfolio": port_df.head(20).to_dict(orient="records"),
        "top20_dual": dual.head(20).to_dict(orient="records"),
        "top15_core_3bps": c3.head(15).to_dict(orient="records"),
        "top15_sat_3bps": sat_df[sat_df.cost_bps == 3].head(15).to_dict(orient="records"),
        "elapsed_sec": time.time() - t_all,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Markdown
    lines = [
        f"# Flex 精密网格回测（{START.date()} → {END.date()}）",
        "",
        f"- 窗口交易日：**{int(win_mask.sum())}** 天（全样本特征用完整历史，收益只计窗口内）",
        f"- IS/OOS：前 60% / 后 40%，切分日 **{is_end.date()}**",
        f"- 官方 RT 截止 `{audit.get('official_end')}`；填充 `{len(audit.get('filled_days') or [])}` 天",
        f"- 网格：核心 **{len(core_df)}** · 卫星 **{len(sat_df)}** · 组合 **{len(port_df)}**",
        f"- CSI300 窗口买入持有：年化 {pct(bh_win['ann_return'])} · 总收益 {pct(bh_win['total_return'])} · 回撤 {pct(bh_win['max_dd'])}",
        f"- 耗时 {summary['elapsed_sec']:.1f}s",
        "",
        "## 生产参数（本窗口）",
        "",
        "| 模式 | 年化 | 总收益 | 最大回撤 | Sharpe | IS年化 | OOS年化 | OOS回撤 | 暴露 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def row_md(name, r):
        if not r:
            return f"| {name} | — | — | — | — | — | — | — | — |"
        return (
            f"| {name} | {pct(r.get('full_ann_return'))} | {pct(r.get('full_total_return'))} | "
            f"{pct(r.get('full_max_dd'))} | {float(r.get('full_sharpe') or 0):.2f} | "
            f"{pct(r.get('is_ann_return'))} | {pct(r.get('oos_ann_return'))} | {pct(r.get('oos_max_dd'))} | "
            f"{pct(r.get('full_exposure_ratio'))} |"
        )

    lines.append(row_md("保守 50/30", cons))
    lines.append(row_md("进取 60/40 单仓满", agg))
    lines.append(row_md("网格综合最优", best))
    lines.append(row_md("双仓最优(w_sat≥15%)", best_dual))

    lines += [
        "",
        "## 组合 Top 15（全网格 score）",
        "",
        "| # | score | 核心规则 | hold | 仓位 | cap | single | sat | cost | 年化 | 回撤 | OOS年化 |",
        "|---:|---:|---|---:|---|---:|---|---|---:|---:|---:|---:|",
    ]
    for i, (_, r) in enumerate(port_df.head(15).iterrows(), 1):
        lines.append(
            f"| {i} | {r.score:.4f} | [{int(r.rt_low)},{int(r.rt_high)})≤{float(r.dd_max):.1%} | {int(r.core_hold)} | "
            f"{float(r.w_core):.0%}/{float(r.w_sat):.0%} | {float(r.total_cap):.0%} | {bool(r.flex_single_full)} | "
            f"{int(r.sat_min)}-{int(r.sat_default)}-{int(r.sat_max)} | {int(r.cost_bps)} | "
            f"{pct(r.full_ann_return)} | {pct(r.full_max_dd)} | {pct(r.oos_ann_return)} |"
        )

    lines += [
        "",
        "## 双仓约束 Top 12（w_core≥20% 且 w_sat≥15%，3bp）",
        "",
        "| # | score | 核心规则 | hold | 仓位 | cap | single | sat | 年化 | 回撤 | OOS年化 |",
        "|---:|---:|---|---:|---|---:|---|---|---:|---:|---:|",
    ]
    for i, (_, r) in enumerate(dual.head(12).iterrows(), 1):
        lines.append(
            f"| {i} | {r.score:.4f} | [{int(r.rt_low)},{int(r.rt_high)})≤{float(r.dd_max):.1%} | {int(r.core_hold)} | "
            f"{float(r.w_core):.0%}/{float(r.w_sat):.0%} | {float(r.total_cap):.0%} | {bool(r.flex_single_full)} | "
            f"{int(r.sat_min)}-{int(r.sat_default)}-{int(r.sat_max)} | "
            f"{pct(r.full_ann_return)} | {pct(r.full_max_dd)} | {pct(r.oos_ann_return)} |"
        )

    lines += [
        "",
        "## 核心规则 Top 12（3bp，本窗口）",
        "",
        "| # | score | rt_low | rt_high | dd | hold | 年化 | 回撤 | OOS年化 | 暴露 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, (_, r) in enumerate(c3.head(12).iterrows(), 1):
        lines.append(
            f"| {i} | {r.score:.4f} | {int(r.rt_low)} | {int(r.rt_high)} | {float(r.dd_max):.1%} | {int(r.core_hold)} | "
            f"{pct(r.full_ann_return)} | {pct(r.full_max_dd)} | {pct(r.oos_ann_return)} | {pct(r.full_exposure_ratio)} |"
        )

    c3r = c3.reset_index(drop=True)
    m = c3r[(c3r.rt_low == 60) & (c3r.rt_high == 80) & np.isclose(c3r.dd_max, -0.05) & (c3r.core_hold == 5)]
    if len(m):
        rank = int(m.index[0]) + 1
        pr = m.iloc[0]
        lines += [
            "",
            f"### 生产核心 60–80 / -5% / 持5 在本窗口 3bp 核心网格排名：**#{rank} / {len(c3r)}**",
            "",
            f"- 年化 {pct(pr.full_ann_return)} · 回撤 {pct(pr.full_max_dd)} · OOS年化 {pct(pr.oos_ann_return)} · 暴露 {pct(pr.full_exposure_ratio)}",
        ]

    lines += [
        "",
        "## 卫星窗 Top 10（3bp）",
        "",
        "| # | score | min | def | max | event | hair | 年化 | 回撤 | OOS年化 |",
        "|---:|---:|---:|---:|---:|---|---|---:|---:|---:|",
    ]
    for i, (_, r) in enumerate(sat_df[sat_df.cost_bps == 3].head(10).iterrows(), 1):
        lines.append(
            f"| {i} | {r.score:.4f} | {int(r.sat_min)} | {int(r.sat_default)} | {int(r.sat_max)} | "
            f"{bool(r.event_exit)} | {bool(r.haircut)} | {pct(r.full_ann_return)} | {pct(r.full_max_dd)} | {pct(r.oos_ann_return)} |"
        )

    lines += [
        "",
        "## 缺口",
        "",
    ]
    if audit.get("filled_days"):
        lines += ["| 日期 | RT | 来源 |", "|---|---:|---|"]
        for d in audit["filled_days"]:
            lines.append(f"| {d['trade_date']} | {d['rt']:.2f} | {d['source']} |")
    else:
        lines.append("无")

    lines += [
        "",
        "## 说明",
        "",
        "1. 收益/回撤**只统计 2025-05-01～2026-07-13**；指标（RT、回撤）用完整历史计算以免暖启动偏差。",
        "2. 价格序列实际到 2026-07-13；7/14 仅有 nowcast RT、无完整指数 bar。",
        "3. 窗口内 IS/OOS=60%/40%，样本短，**尖端参数过拟合风险高**，生产参数需单独看稳健性。",
        "4. 研究回测，非投资建议。",
        "",
    ]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("Wrote", OUT / "report.md")
    print("=== DONE ===", f"{time.time() - t_all:.1f}s")


if __name__ == "__main__":
    main()
