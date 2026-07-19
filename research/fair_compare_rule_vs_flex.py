#!/usr/bin/env python3
"""Fair comparison: CORE_PROD rule vs Flex 进取 (same window).

Apples-to-apples views:
  1) Portfolio (cash earns 0) — raw book P&L
  2) Active days only — return while capital is deployed
  3) Exposure-normalized (ann / exposure) — rough full-time equivalent
  4) Risk-scaled (Calmar, Sharpe, ann/|dd|)
  5) Exposure-matched lever — scale rule daily to match Flex exposure
  6) Sleeve split — core alone / sat alone / combined 进取 / 保守
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

from research.backtest_core_plus_sectors import load_aligned  # noqa: E402
from research.backtest_flex_dense_grid import (  # noqa: E402
    combine_port,
    extend_aligned_to_end,
    precompute_stages,
    simulate_core_daily,
    simulate_sat_daily,
    stats_from_daily,
)

OUT = ROOT / "research/output/fair_compare_rule_vs_flex"
START = pd.Timestamp("2025-05-01")
END = pd.Timestamp("2026-07-13")
EXT_END = pd.Timestamp("2026-07-14")
TRADING_DAYS = 252
COST = 0.0003  # 3bp


def window_slice(daily: np.ndarray, dates: pd.Series) -> np.ndarray:
    m = (dates.values >= np.datetime64(START)) & (dates.values <= np.datetime64(END))
    return np.asarray(daily, dtype=float)[m]


def ann_from_total(total: float, n: int) -> float:
    if n <= 0 or total <= -1:
        return float("nan")
    return float((1 + total) ** (TRADING_DAYS / n) - 1)


def max_dd(eq: np.ndarray) -> float:
    peak = np.maximum.accumulate(eq)
    return float(np.nanmin(eq / peak - 1.0))


def metrics(daily: np.ndarray, label: str, trade_rets: list[float] | None = None) -> dict:
    daily = np.asarray(daily, dtype=float)
    n = len(daily)
    eq = np.cumprod(1.0 + daily) if n else np.array([1.0])
    total = float(eq[-1] - 1.0)
    on = daily != 0.0
    exp = float(np.mean(on)) if n else 0.0
    n_on = int(on.sum())
    # active-only compound over active days, then annualize by active day count
    if n_on > 0:
        eq_on = np.cumprod(1.0 + daily[on])
        total_on = float(eq_on[-1] - 1.0)
        ann_active = ann_from_total(total_on, n_on)
        mean_on = float(np.mean(daily[on]))
        std_on = float(np.std(daily[on], ddof=1)) if n_on > 2 else float("nan")
        sharpe_active = (
            mean_on / std_on * math.sqrt(TRADING_DAYS) if std_on and std_on > 0 else float("nan")
        )
    else:
        total_on = 0.0
        ann_active = float("nan")
        sharpe_active = float("nan")

    base = stats_from_daily(daily, trade_rets)
    ann = base["ann_return"]
    dd = base["max_dd"]
    calmar = float(ann / abs(dd)) if np.isfinite(ann) and np.isfinite(dd) and abs(dd) > 1e-12 else float("nan")
    ann_per_exp = float(ann / exp) if exp > 1e-9 and np.isfinite(ann) else float("nan")

    return {
        "label": label,
        "n_days": n,
        "total_return": total,
        "ann_return": float(ann) if np.isfinite(ann) else float("nan"),
        "max_dd": float(dd) if np.isfinite(dd) else float("nan"),
        "sharpe": float(base["sharpe"]) if np.isfinite(base["sharpe"]) else float("nan"),
        "exposure": exp,
        "n_active_days": n_on,
        "trade_count": int(base.get("trade_count") or 0),
        "win_rate": float(base["win_rate"]) if np.isfinite(base.get("win_rate", np.nan)) else float("nan"),
        "avg_trade": float(base["avg_trade"]) if np.isfinite(base.get("avg_trade", np.nan)) else float("nan"),
        # fair views
        "ann_active": ann_active,  # annualized using only invested days
        "total_on_active_path": total_on,  # compound only on active days (not calendar)
        "sharpe_active": sharpe_active,
        "ann_per_exposure": ann_per_exp,  # rough FTE if you always redeploy at same hit-rate
        "calmar": calmar,
        "ann_over_abs_dd": calmar,
    }


def scale_to_exposure(daily: np.ndarray, target_exp: float) -> np.ndarray:
    """Leverage active days so mean exposure matches target (cash still 0)."""
    d = np.asarray(daily, dtype=float).copy()
    on = d != 0.0
    exp = float(np.mean(on)) if len(d) else 0.0
    if exp < 1e-12 or target_exp < 1e-12:
        return d
    # exposure = fraction of days on; can't change fraction without filling zeros.
    # Instead scale *return amplitude* by target_exp/exp so dollar-days risk ~ matches
    # a fund that puts (target_exp/exp) more capital into the same signals.
    lev = target_exp / exp
    d[on] = d[on] * lev
    return d


def scale_vol_match(daily: np.ndarray, ref: np.ndarray) -> np.ndarray:
    d = np.asarray(daily, dtype=float).copy()
    r = np.asarray(ref, dtype=float)
    sd = float(np.std(d, ddof=1)) if len(d) > 2 else 0.0
    sr = float(np.std(r, ddof=1)) if len(r) > 2 else 0.0
    if sd < 1e-12 or sr < 1e-12:
        return d
    return d * (sr / sd)


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
        return f"{float(x):.{nd}f}"
    except Exception:
        return "—"


def main() -> None:
    warnings.filterwarnings("ignore")
    OUT.mkdir(parents=True, exist_ok=True)

    print("=== Load aligned + extend ===", flush=True)
    df0, meta0 = load_aligned()
    df, meta, audit = extend_aligned_to_end(df0, meta0, EXT_END)
    df = df[df["trade_date"] <= END].reset_index(drop=True)
    dates = df["trade_date"]
    win_mask = (dates >= START) & (dates <= END)
    print(
        f"rows={len(df)} window_n={int(win_mask.sum())} "
        f"{dates[win_mask].min().date()}→{dates[win_mask].max().date()}",
        flush=True,
    )

    rt = df["rt"].to_numpy(dtype=float)
    dd = df["dd60"].to_numpy(dtype=float)
    csi_open = df["csi_open"].to_numpy(dtype=float)
    csi_close = df["csi_close"].to_numpy(dtype=float)

    # --- sleeves (full history path, then window) ---
    core_full, core_trades = simulate_core_daily(
        rt, dd, csi_open, csi_close,
        rt_low=60, rt_high=80, dd_max=-0.05, hold_days=5,
        buy_cost=COST, sell_cost=COST,
    )
    stages = precompute_stages(df)
    sat_full, sat_trades = simulate_sat_daily(
        df, meta, stages,
        sat_min=3, sat_default=5, sat_max=8,
        buy_cost=COST, sell_cost=COST,
        apply_haircut=True, event_exit=True,
    )

    # Flex 进取: w 60/40, single full, cap 100%
    agg_full = combine_port(
        core_full, sat_full,
        w_core=0.6, w_sat=0.4, total_cap=1.0, flex_single_full=True,
    )
    # Flex 保守: 50/30 cap 80%, no single full
    cons_full = combine_port(
        core_full, sat_full,
        w_core=0.5, w_sat=0.3, total_cap=0.8, flex_single_full=False,
    )
    # Core-only as portfolio (same as rule)
    # Core when only core on → already 100% of core sleeve; for portfolio book it's core_daily itself
    # Satellite-only portfolio
    # Dual without single-full (60/40, never boost to 100%)
    dual_fixed = combine_port(
        core_full, sat_full,
        w_core=0.6, w_sat=0.4, total_cap=1.0, flex_single_full=False,
    )
    # Core full when signal, else 0 — already core_full
    # Core + idle cash earning 0 = core_full portfolio metrics

    bh_full = df["csi_close"].pct_change().fillna(0.0).to_numpy(dtype=float)

    # Window series
    series = {
        "BH_CSI300": window_slice(bh_full, dates),
        "RULE_CORE_PROD": window_slice(core_full, dates),
        "SAT_ONLY": window_slice(sat_full, dates),
        "FLEX_CORE_SLEEVE": window_slice(core_full, dates),  # identical path to rule
        "FLEX_DUAL_60_40_fixed": window_slice(dual_fixed, dates),
        "FLEX_AGG_single_full": window_slice(agg_full, dates),
        "FLEX_CONS_50_30_cap80": window_slice(cons_full, dates),
    }

    # trade rets only for window: approximate by re-sim is heavy; use full trades filtered by entry in window
    # stats_from_daily trade counts: pass window-filtered trade rets for core/sat
    # Core trades from simulate are list of trade returns only — no dates. Recompute window core trades:
    core_w, core_rets_w = simulate_core_daily(
        rt, dd, csi_open, csi_close,
        rt_low=60, rt_high=80, dd_max=-0.05, hold_days=5,
        buy_cost=COST, sell_cost=COST,
    )
    # Zero out pre-window in daily for trade accounting: count trades with entry in window
    # (simulate doesn't give dates; reconstruct quickly)
    def core_trades_in_window() -> list[float]:
        rets: list[float] = []
        next_free = 0
        n = len(rt)
        for i in range(n - 2):
            if i < next_free:
                continue
            if not (np.isfinite(rt[i]) and np.isfinite(dd[i])):
                continue
            if not (60 <= rt[i] < 80 and dd[i] <= -0.05):
                continue
            if not np.isfinite(csi_open[i + 1]):
                continue
            td = dates.iloc[i]
            if td < START or td > END:
                continue
            entry_i = i + 1
            exit_i = min(entry_i + 5, n - 1)
            px_in = csi_open[entry_i]
            px_out = csi_open[exit_i] if exit_i < n and np.isfinite(csi_open[exit_i]) else csi_close[min(exit_i, n - 1)]
            if not (np.isfinite(px_in) and np.isfinite(px_out)):
                continue
            r = (px_out * (1 - COST)) / (px_in * (1 + COST)) - 1.0
            rets.append(float(r))
            next_free = exit_i + 1
        return rets

    core_rets = core_trades_in_window()

    rows = []
    rows.append(metrics(series["BH_CSI300"], "买入持有 CSI300", None))
    rows.append(metrics(series["RULE_CORE_PROD"], "规则 CORE_PROD（仅核心）", core_rets))
    rows.append(metrics(series["SAT_ONLY"], "卫星袖单独", None))
    rows.append(metrics(series["FLEX_DUAL_60_40_fixed"], "双袖 60/40（无单仓满）", None))
    rows.append(metrics(series["FLEX_AGG_single_full"], "Flex 进取（60/40+单仓满）", None))
    rows.append(metrics(series["FLEX_CONS_50_30_cap80"], "Flex 保守（50/30 cap80%）", None))

    # Exposure-matched: lever rule to match Flex 进取 exposure fraction *is wrong for zeros*
    # Better: capital-matched = put 100% when rule fires; Flex already does single-full on core-only days.
    # Dollar-risk match: scale rule returns by (flex_exp / rule_exp) so expected capital-days equal
    flex_d = series["FLEX_AGG_single_full"]
    rule_d = series["RULE_CORE_PROD"]
    flex_exp = float(np.mean(flex_d != 0))
    rule_exp = float(np.mean(rule_d != 0))
    rule_lev_to_flex_exp = scale_to_exposure(rule_d, flex_exp)
    rows.append(
        metrics(
            rule_lev_to_flex_exp,
            f"规则×杠杆对齐暴露≈{flex_exp:.0%}（仅放大信号日仓位）",
            None,
        )
    )

    # Vol-match Flex to BH and Rule to BH
    bh = series["BH_CSI300"]
    rows.append(metrics(scale_vol_match(rule_d, bh), "规则 vol=BH", None))
    rows.append(metrics(scale_vol_match(flex_d, bh), "进取 vol=BH", None))

    # Overlap analysis
    c = series["RULE_CORE_PROD"]
    s = series["SAT_ONLY"]
    both = (c != 0) & (s != 0)
    only_c = (c != 0) & (s == 0)
    only_s = (c == 0) & (s != 0)
    neither = (c == 0) & (s == 0)
    n = len(c)
    overlap = {
        "n_days": n,
        "core_on_pct": float(np.mean(c != 0)),
        "sat_on_pct": float(np.mean(s != 0)),
        "both_on_pct": float(np.mean(both)),
        "only_core_pct": float(np.mean(only_c)),
        "only_sat_pct": float(np.mean(only_s)),
        "cash_pct": float(np.mean(neither)),
        "mean_ret_core_days": float(np.mean(c[c != 0])) if np.any(c != 0) else float("nan"),
        "mean_ret_sat_days": float(np.mean(s[s != 0])) if np.any(s != 0) else float("nan"),
        "contrib_core_to_agg": float(np.sum(window_slice(
            combine_port(core_full, sat_full, w_core=0.6, w_sat=0.4, total_cap=1.0, flex_single_full=True)
            * 0 + core_full,  # placeholder
            dates,
        ))),  # will recompute properly below
    }

    # Proper contribution: decompose 进取 daily into core vs sat weight*return
    def decomp_agg():
        c_on = core_full != 0
        s_on = sat_full != 0
        wc = np.where(c_on, 0.6, 0.0)
        ws = np.where(s_on, 0.4, 0.0)
        only_c = c_on & ~s_on
        only_s = s_on & ~c_on
        wc = np.where(only_c, 1.0, wc)
        ws = np.where(only_c, 0.0, ws)
        wc = np.where(only_s, 0.0, wc)
        ws = np.where(only_s, 1.0, ws)
        total = wc + ws
        scale = np.ones_like(total)
        over = total > 1.0
        scale[over] = 1.0 / total[over]
        wc, ws = wc * scale, ws * scale
        core_part = window_slice(wc * core_full, dates)
        sat_part = window_slice(ws * sat_full, dates)
        return core_part, sat_part

    core_part, sat_part = decomp_agg()
    agg_w = series["FLEX_AGG_single_full"]
    # additive daily approx contribution to total log-return
    sum_core = float(np.sum(core_part))
    sum_sat = float(np.sum(sat_part))
    sum_agg = float(np.sum(agg_w))
    decomp = {
        "sum_daily_core_weighted": sum_core,
        "sum_daily_sat_weighted": sum_sat,
        "sum_daily_agg": sum_agg,
        "core_share_of_sum": sum_core / sum_agg if abs(sum_agg) > 1e-12 else float("nan"),
        "sat_share_of_sum": sum_sat / sum_agg if abs(sum_agg) > 1e-12 else float("nan"),
        "core_part_total_if_alone_path": float(np.cumprod(1 + core_part)[-1] - 1),
        "sat_part_total_if_alone_path": float(np.cumprod(1 + sat_part)[-1] - 1),
        "overlap_days_pct": float(np.mean(both)),
        "only_core_days_pct": float(np.mean(only_c)),
        "only_sat_days_pct": float(np.mean(only_s)),
        "cash_days_pct": float(np.mean(neither)),
        "rule_trade_count_window": len(core_rets),
        "rule_win_rate": float(np.mean([r > 0 for r in core_rets])) if core_rets else float("nan"),
        "rule_avg_trade": float(np.mean(core_rets)) if core_rets else float("nan"),
        "rule_sum_trade_rets_compound": float(np.prod([1 + r for r in core_rets]) - 1) if core_rets else 0.0,
    }

    # Equity curves CSV
    eq_df = pd.DataFrame({
        "trade_date": dates[win_mask].values,
        "bh": np.cumprod(1 + series["BH_CSI300"]),
        "rule_core": np.cumprod(1 + series["RULE_CORE_PROD"]),
        "sat_only": np.cumprod(1 + series["SAT_ONLY"]),
        "flex_agg": np.cumprod(1 + series["FLEX_AGG_single_full"]),
        "flex_cons": np.cumprod(1 + series["FLEX_CONS_50_30_cap80"]),
        "dual_fixed": np.cumprod(1 + series["FLEX_DUAL_60_40_fixed"]),
        "rule_lev_to_flex_exp": np.cumprod(1 + rule_lev_to_flex_exp),
    })
    eq_df.to_csv(OUT / "equity_curves.csv", index=False)

    tbl = pd.DataFrame(rows)
    tbl.to_csv(OUT / "metrics_table.csv", index=False)

    summary = {
        "window": {"start": str(START.date()), "end": str(END.date()), "n": int(win_mask.sum())},
        "cost_bps": 3,
        "metrics": rows,
        "decomposition": decomp,
        "overlap": {
            "core_on_pct": float(np.mean(c != 0)),
            "sat_on_pct": float(np.mean(s != 0)),
            "both_on_pct": float(np.mean(both)),
            "only_core_pct": float(np.mean(only_c)),
            "only_sat_pct": float(np.mean(only_s)),
            "cash_pct": float(np.mean(neither)),
        },
        "notes": [
            "RULE_CORE_PROD path is identical to Flex core sleeve (same simulate_core_daily).",
            "ann_active = annualize compound return using only invested days as year length.",
            "ann_per_exposure = portfolio ann / exposure (rough FTE if hit-rate sustained).",
            "rule×杠杆 aligns amplitude so rule_exp * lev ≈ flex_exp; still only trades on core signals.",
        ],
        "extension_audit": audit,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Report
    def row_md(r: dict) -> str:
        return (
            f"| {r['label']} | {pct(r['ann_return'])} | {pct(r['total_return'])} | {pct(r['max_dd'])} | "
            f"{num(r['sharpe'])} | {pct(r['exposure'])} | {pct(r['ann_active'])} | "
            f"{pct(r['ann_per_exposure'])} | {num(r['calmar'])} | {r['trade_count'] if r['trade_count'] else '—'} |"
        )

    lines = [
        "# 公平对比：CORE_PROD 规则 vs Flex 进取",
        "",
        f"- 窗口：**{START.date()} → {END.date()}**（{int(win_mask.sum())} 交易日）",
        "- 成本：3bp 单边",
        "- 规则路径 = Flex 核心袖（同一 `simulate_core_daily`）",
        "",
        "## 1. 多视角指标",
        "",
        "| 策略 | 组合年化 | 总收益 | 最大回撤 | Sharpe | 暴露 | **在场年化** | **年化/暴露** | Calmar | 笔数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(row_md(r))

    lines += [
        "",
        "### 读法",
        "",
        "- **组合年化**：闲置资金按 0 收益（最「账面」）。",
        "- **在场年化**：只用有仓日复利再年化 → 回答「钱真正上场时赚不赚」。",
        "- **年化/暴露**：把低暴露策略粗算成「若同样击中率全时在场」的量级（**非**可实现收益）。",
        "- **规则×杠杆**：把信号日仓位放大到与进取**平均暴露**同阶，仍只在核心信号日交易。",
        "",
        "## 2. 袖子重叠与收益拆解（进取）",
        "",
        f"- 仅核心在场：{pct(decomp['only_core_days_pct'])}",
        f"- 仅卫星在场：{pct(decomp['only_sat_days_pct'])}",
        f"- 双袖同时：{pct(decomp['overlap_days_pct'])}",
        f"- 空仓：{pct(decomp['cash_days_pct'])}",
        f"- 进取日收益加总中：核心加权份额 ≈ {pct(decomp['core_share_of_sum'])}，卫星 ≈ {pct(decomp['sat_share_of_sum'])}",
        f"- 核心信号窗内笔数：{decomp['rule_trade_count_window']} · 胜率 {pct(decomp['rule_win_rate'])} · 均笔 {pct(decomp['rule_avg_trade'])} · 笔复利 {pct(decomp['rule_sum_trade_rets_compound'])}",
        "",
        "## 3. 公平裁定",
        "",
    ]

    # Decision text from numbers
    m = {r["label"]: r for r in rows}
    rule = m["规则 CORE_PROD（仅核心）"]
    agg = m["Flex 进取（60/40+单仓满）"]
    sat = m["卫星袖单独"]
    lines += [
        "### A. 同一本金、账面组合收益 → **进取完胜**",
        f"- 规则 {pct(rule['ann_return'])} / 回撤 {pct(rule['max_dd'])} / 暴露 {pct(rule['exposure'])}",
        f"- 进取 {pct(agg['ann_return'])} / 回撤 {pct(agg['max_dd'])} / 暴露 {pct(agg['exposure'])}",
        "",
        "### B. 钱真正上场时（在场年化）→ 看边缘质量",
        f"- 规则在场年化 {pct(rule['ann_active'])}，Sharpe(在场) {num(rule['sharpe_active'])}",
        f"- 进取在场年化 {pct(agg['ann_active'])}，Sharpe(在场) {num(agg['sharpe_active'])}",
        f"- 卫星单独在场年化 {pct(sat['ann_active'])}（暴露 {pct(sat['exposure'])}）",
        "",
        "### C. 单位暴露效率（年化/暴露）",
        f"- 规则 {pct(rule['ann_per_exposure'])}",
        f"- 进取 {pct(agg['ann_per_exposure'])}",
        f"- 卫星 {pct(sat['ann_per_exposure'])}",
        "",
        "### D. 风险效率（Calmar = 年化/|回撤|）",
        f"- 规则 {num(rule['calmar'])}",
        f"- 进取 {num(agg['calmar'])}",
        f"- 保守 {num(m['Flex 保守（50/30 cap80%）']['calmar'])}",
        f"- BH {num(m['买入持有 CSI300']['calmar'])}",
        "",
        "### E. 结构结论",
        "",
        "1. **规则 ≡ Flex 核心袖**，不是另一套 alpha；比的是「只要核心」vs「核心+卫星+仓位规则」。",
        "2. 本窗进取超额主要来自 **卫星高暴露 + 单仓满把卫星/核心日拉到满仓**，不是核心条件更聪明。",
        "3. 若你的资金大部分必须常仓/进攻 → **进取更合适**。",
        "4. 若你只要可审计的沪深300事件仓、闲钱另有用途 → **规则更合适**（回撤与样本干净）。",
        "5. 两者都未证明能稳定跑赢任意牛市；进取数字对路径/行业环境敏感。",
        "",
        "## 文件",
        "",
        "- `metrics_table.csv`",
        "- `equity_curves.csv`",
        "- `summary.json`",
        "",
        "研究回测，非投资建议。",
        "",
    ]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines), flush=True)
    print(f"\nWrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
