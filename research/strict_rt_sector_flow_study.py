#!/usr/bin/env python3
"""Strict risk-temperature vs sector capital-preference study.

Proxy for "资金流向":
  We do NOT observe official fund flow. We use relative performance:
    excess_return = sector_return - CSI300_return
  as a strict, auditable proxy for where risk capital relatively rotates.

Questions answered:
  1) When RT rises, which sectors outperform / underperform CSI300?
  2) When RT falls, where does relative capital rotate?
  3) When RT is high and starts cooling, which sectors look like "避险" (defensive outperformers)?
  4) Include Hang Seng Tech (HSTECH) alongside Shenwan L1 sectors.

Run from repo root:
  python3 research/strict_rt_sector_flow_study.py
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research/output/sector_flow"
CHARTS = OUT / "charts"
RISK_PATH = ROOT / "data/calculated/risk_components.csv"
SECTOR_L1 = ROOT / "data/normalized/sw_level1_sector_history.csv"
INDEX_HS300 = ROOT / "data/raw/indices/sh000300.csv"
HSTECH_CACHE = ROOT / "data/raw/indices/hstech.csv"

MIN_EVENT_N = 25  # hard sample floor per event-sector
MIN_CORR_N = 60
HORIZONS = (1, 5, 10, 20)
RANDOM_SEED = 42


def ensure_dirs() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)


def _pct(x, d=2) -> str:
    if x is None or not np.isfinite(float(x)):
        return "--"
    return f"{float(x) * 100:.{d}f}%"


def _f(x, d=3) -> str:
    if x is None or not np.isfinite(float(x)):
        return "--"
    return f"{float(x):.{d}f}"


def fetch_hstech() -> pd.DataFrame:
    """Fetch Hang Seng TECH Index daily bars and cache.

    Prefer Sina (more proxy-friendly); fall back to Eastmoney.
    """
    import os

    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")
    import akshare as ak

    raw = None
    source = ""
    errors: list[str] = []
    for label, fn in [
        ("AKSHARE_HK_INDEX_SINA", lambda: ak.stock_hk_index_daily_sina(symbol="HSTECH")),
        ("AKSHARE_HK_INDEX_EM", lambda: ak.stock_hk_index_daily_em(symbol="HSTECH")),
    ]:
        try:
            cand = fn()
            if cand is not None and not cand.empty:
                raw = cand
                source = label
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
    if raw is None or raw.empty:
        raise RuntimeError("Failed to fetch HSTECH; " + " | ".join(errors[:2]))

    df = raw.rename(columns={"latest": "close"}).copy()
    if "close" not in df.columns:
        # sina already uses close
        pass
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for c in ["open", "high", "low", "close"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["symbol"] = "HSTECH"
    df["name"] = "恒生科技"
    df["source"] = source
    df["fetch_time"] = pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")
    cols = [c for c in ["date", "open", "high", "low", "close", "symbol", "name", "source", "fetch_time"] if c in df.columns]
    out = df[cols].dropna(subset=["date", "close"]).sort_values("date")
    HSTECH_CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(HSTECH_CACHE, index=False)
    return out


def load_hstech() -> pd.DataFrame:
    try:
        fresh = fetch_hstech()
        print(f"HSTECH fetched rows={len(fresh)} {fresh['date'].min()}→{fresh['date'].max()}")
        return fresh
    except Exception as exc:  # noqa: BLE001
        print(f"WARN HSTECH fetch failed: {exc}")
        if HSTECH_CACHE.exists():
            cached = pd.read_csv(HSTECH_CACHE)
            print(f"Using cached HSTECH rows={len(cached)}")
            return cached
        raise


def load_base() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    risk = pd.read_csv(RISK_PATH)
    risk["trade_date"] = pd.to_datetime(risk["trade_date"], errors="coerce")
    risk["risk_temperature"] = pd.to_numeric(risk["risk_temperature"], errors="coerce")
    risk = risk.dropna(subset=["trade_date", "risk_temperature"]).sort_values("trade_date")
    risk["date"] = risk["trade_date"].dt.strftime("%Y-%m-%d")
    risk["rt_d1"] = risk["risk_temperature"].diff()
    risk["rt_d5"] = risk["risk_temperature"] - risk["risk_temperature"].shift(5)
    risk["rt_d10"] = risk["risk_temperature"] - risk["risk_temperature"].shift(10)
    risk["rt_rollmax_5"] = risk["risk_temperature"].rolling(5, min_periods=3).max()
    risk["rt_rollmax_10"] = risk["risk_temperature"].rolling(10, min_periods=5).max()
    risk["rt_ma5"] = risk["risk_temperature"].rolling(5, min_periods=3).mean()

    hs = pd.read_csv(INDEX_HS300)
    hs["date"] = pd.to_datetime(hs["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    hs["close"] = pd.to_numeric(hs["close"], errors="coerce")
    hs = hs.dropna(subset=["date", "close"]).sort_values("date")
    hs["bench_ret_1d"] = hs["close"].pct_change()
    for h in HORIZONS:
        hs[f"bench_fwd_{h}d"] = hs["close"].shift(-h) / hs["close"] - 1
        hs[f"bench_past_{h}d"] = hs["close"] / hs["close"].shift(h) - 1

    sectors = pd.read_csv(SECTOR_L1)
    sectors["date"] = pd.to_datetime(sectors["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    sectors["close"] = pd.to_numeric(sectors["close"], errors="coerce")
    sectors["symbol"] = sectors["symbol"].astype(str)
    sectors = sectors.dropna(subset=["date", "symbol", "name", "close"]).sort_values(["symbol", "date"])

    hstech = load_hstech()
    hstech["date"] = pd.to_datetime(hstech["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    hstech["close"] = pd.to_numeric(hstech["close"], errors="coerce")
    hstech["symbol"] = "HSTECH"
    hstech["name"] = "恒生科技"
    keep = ["date", "symbol", "name", "close"]
    sectors = pd.concat([sectors[keep], hstech[keep]], ignore_index=True)

    return risk, hs, sectors


def build_panel(risk: pd.DataFrame, hs: pd.DataFrame, sectors: pd.DataFrame) -> pd.DataFrame:
    """Date-aligned panel: each row = sector x date with RT and excess returns."""
    # Align on intersection of A-share trading days with RT (HSTECH may miss some A days)
    base_dates = set(risk["date"]) & set(hs["date"])
    frames = []
    for (symbol, name), g in sectors.groupby(["symbol", "name"], sort=True):
        g = g.sort_values("date").copy()
        g["ret_1d"] = g["close"].pct_change()
        for h in HORIZONS:
            g[f"fwd_{h}d"] = g["close"].shift(-h) / g["close"] - 1
            g[f"past_{h}d"] = g["close"] / g["close"].shift(h) - 1
        g = g[g["date"].isin(base_dates)]
        frames.append(g)
    sec = pd.concat(frames, ignore_index=True)
    panel = sec.merge(
        hs[["date", "close", "bench_ret_1d"] + [f"bench_fwd_{h}d" for h in HORIZONS] + [f"bench_past_{h}d" for h in HORIZONS]].rename(
            columns={"close": "bench_close"}
        ),
        on="date",
        how="inner",
    )
    panel = panel.merge(
        risk[
            [
                "date",
                "risk_temperature",
                "rt_d1",
                "rt_d5",
                "rt_d10",
                "rt_rollmax_5",
                "rt_rollmax_10",
                "rt_ma5",
            ]
        ],
        on="date",
        how="inner",
    )
    panel["excess_1d"] = panel["ret_1d"] - panel["bench_ret_1d"]
    for h in HORIZONS:
        panel[f"excess_fwd_{h}d"] = panel[f"fwd_{h}d"] - panel[f"bench_fwd_{h}d"]
        panel[f"excess_past_{h}d"] = panel[f"past_{h}d"] - panel[f"bench_past_{h}d"]
    panel = panel.sort_values(["symbol", "date"]).reset_index(drop=True)
    return panel


def define_events(risk: pd.DataFrame) -> pd.DataFrame:
    """Strict boolean event flags on risk dates only."""
    e = risk[["date", "risk_temperature", "rt_d1", "rt_d5", "rt_d10", "rt_rollmax_5", "rt_rollmax_10"]].copy()
    # rising / falling by 5-day change magnitude (absolute thresholds + quantile robustness later)
    e["evt_rt_rising_hard"] = e["rt_d5"] >= 5.0
    e["evt_rt_falling_hard"] = e["rt_d5"] <= -5.0
    e["evt_rt_rising_soft"] = e["rt_d5"] >= 3.0
    e["evt_rt_falling_soft"] = e["rt_d5"] <= -3.0

    # enter high / exit high
    prev = e["risk_temperature"].shift(1)
    e["evt_enter_high_60"] = (prev < 60) & (e["risk_temperature"] >= 60)
    e["evt_enter_high_70"] = (prev < 70) & (e["risk_temperature"] >= 70)
    e["evt_exit_high_60"] = (prev >= 60) & (e["risk_temperature"] < 60)

    # high & cooling: recently high and now falling
    e["evt_high_cooling"] = (
        (e["rt_rollmax_10"] >= 65)
        & (e["risk_temperature"] >= 55)
        & (e["rt_d5"] <= -3.0)
        & (e["rt_d1"] < 0)
    )
    e["evt_high_cooling_strict"] = (
        (e["rt_rollmax_5"] >= 70)
        & (e["risk_temperature"] >= 60)
        & (e["rt_d5"] <= -5.0)
    )

    # panic regime days (level, not transition)
    e["evt_regime_calm"] = e["risk_temperature"] < 40
    e["evt_regime_caution"] = (e["risk_temperature"] >= 40) & (e["risk_temperature"] < 60)
    e["evt_regime_high"] = (e["risk_temperature"] >= 60) & (e["risk_temperature"] < 75)
    e["evt_regime_panic"] = e["risk_temperature"] >= 75

    # top/bottom quintile of RT 5d change (data-driven, no magic number only)
    q80 = e["rt_d5"].quantile(0.80)
    q20 = e["rt_d5"].quantile(0.20)
    e["evt_rt_rising_q80"] = e["rt_d5"] >= q80
    e["evt_rt_falling_q20"] = e["rt_d5"] <= q20
    e.attrs["rt_d5_q80"] = float(q80) if np.isfinite(q80) else np.nan
    e.attrs["rt_d5_q20"] = float(q20) if np.isfinite(q20) else np.nan
    return e


EVENT_META = {
    "evt_rt_rising_hard": ("升温(硬, ΔRT5≥+5)", "相对资金可能涌入的进攻/弹性板块"),
    "evt_rt_rising_q80": ("升温(分位, ΔRT5≥P80)", "升温日资金偏好（分位定义）"),
    "evt_rt_falling_hard": ("降温(硬, ΔRT5≤-5)", "降温时相对抽血/流入"),
    "evt_rt_falling_q20": ("降温(分位, ΔRT5≤P20)", "降温日资金偏好（分位定义）"),
    "evt_high_cooling": ("高位回落(宽松)", "高温后降温：避险去向"),
    "evt_high_cooling_strict": ("高位回落(严格)", "高位回落严格版：避险去向"),
    "evt_enter_high_60": ("进入高风险(≥60)", "风险刚抬升时相对强弱"),
    "evt_enter_high_70": ("进入更高风险(≥70)", "风险进一步抬升"),
    "evt_exit_high_60": ("退出高风险(<60)", "风险回落穿越60"),
    "evt_regime_high": ("高风险区间[60,75)", "处于高风险水平的板块偏好"),
    "evt_regime_panic": ("恐慌区间≥75", "恐慌水平板块偏好"),
    "evt_regime_calm": ("平静区间<40", "低风险环境板块偏好"),
}


def event_stats(panel: pd.DataFrame, events: pd.DataFrame, event_col: str, ret_col: str) -> pd.DataFrame:
    dates = set(events.loc[events[event_col].fillna(False), "date"])
    sub = panel[panel["date"].isin(dates)].copy()
    rows = []
    for (symbol, name), g in sub.groupby(["symbol", "name"], sort=True):
        vals = pd.to_numeric(g[ret_col], errors="coerce").dropna()
        n = int(len(vals))
        if n == 0:
            continue
        mean = float(vals.mean())
        # one-sample t-test vs 0
        if n >= 3 and vals.std(ddof=1) > 0:
            t_stat, p_val = stats.ttest_1samp(vals, 0.0)
        else:
            t_stat, p_val = np.nan, np.nan
        rows.append(
            {
                "event": event_col,
                "event_label": EVENT_META.get(event_col, (event_col, ""))[0],
                "return_metric": ret_col,
                "symbol": str(symbol),
                "name": str(name),
                "n": n,
                "mean_excess": mean,
                "median_excess": float(vals.median()),
                "win_rate": float((vals > 0).mean()),
                "std": float(vals.std(ddof=1)) if n > 1 else np.nan,
                "t_stat": float(t_stat) if np.isfinite(t_stat) else np.nan,
                "p_value": float(p_val) if np.isfinite(p_val) else np.nan,
                "significant_5pct": bool(np.isfinite(p_val) and p_val < 0.05 and n >= MIN_EVENT_N),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["abs_mean"] = out["mean_excess"].abs()
    return out.sort_values(["mean_excess"], ascending=False)


def rank_hit_rate(panel: pd.DataFrame, events: pd.DataFrame, event_col: str, ret_col: str, top_k: int = 5) -> pd.DataFrame:
    """How often each sector is top-k / bottom-k by excess on event days."""
    dates = sorted(events.loc[events[event_col].fillna(False), "date"].unique())
    top_counts: dict[str, int] = {}
    bot_counts: dict[str, int] = {}
    names: dict[str, str] = {}
    valid_days = 0
    for d in dates:
        day = panel[(panel["date"] == d) & panel[ret_col].notna()][["symbol", "name", ret_col]]
        if len(day) < 10:
            continue
        valid_days += 1
        day = day.sort_values(ret_col, ascending=False)
        for _, r in day.head(top_k).iterrows():
            top_counts[r["symbol"]] = top_counts.get(r["symbol"], 0) + 1
            names[r["symbol"]] = r["name"]
        for _, r in day.tail(top_k).iterrows():
            bot_counts[r["symbol"]] = bot_counts.get(r["symbol"], 0) + 1
            names[r["symbol"]] = r["name"]
    rows = []
    symbols = set(top_counts) | set(bot_counts)
    for s in symbols:
        rows.append(
            {
                "event": event_col,
                "symbol": s,
                "name": names.get(s, s),
                "event_days": valid_days,
                "top_k_hits": top_counts.get(s, 0),
                "bottom_k_hits": bot_counts.get(s, 0),
                "top_k_rate": top_counts.get(s, 0) / valid_days if valid_days else np.nan,
                "bottom_k_rate": bot_counts.get(s, 0) / valid_days if valid_days else np.nan,
                "net_preference": (top_counts.get(s, 0) - bot_counts.get(s, 0)) / valid_days if valid_days else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("net_preference", ascending=False)


def correlation_table(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (symbol, name), g in panel.groupby(["symbol", "name"], sort=True):
        g = g.sort_values("date")
        for metric, col in [
            ("corr_rt_excess_fwd5", "excess_fwd_5d"),
            ("corr_rt_excess_fwd10", "excess_fwd_10d"),
            ("corr_d5rt_excess_fwd5", "excess_fwd_5d"),
            ("corr_rt_sync_excess1", "excess_1d"),
        ]:
            if metric.startswith("corr_d5rt"):
                x = g["rt_d5"]
            else:
                x = g["risk_temperature"]
            y = g[col]
            frame = pd.concat([x, y], axis=1).dropna()
            n = len(frame)
            if n < MIN_CORR_N:
                corr = p = np.nan
            else:
                corr, p = stats.pearsonr(frame.iloc[:, 0], frame.iloc[:, 1])
            rows.append(
                {
                    "symbol": str(symbol),
                    "name": str(name),
                    "metric": metric,
                    "n": n,
                    "corr": float(corr) if np.isfinite(corr) else np.nan,
                    "p_value": float(p) if np.isfinite(p) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def oos_stability(panel: pd.DataFrame, events: pd.DataFrame, event_col: str, ret_col: str = "excess_fwd_5d") -> pd.DataFrame:
    """Split sample at 2024-01-01; require same sign mean excess in IS and OOS with n floors."""
    split = "2024-01-01"
    rows = []
    for period, mask_dates in [
        ("IS", events["date"] < split),
        ("OOS", events["date"] >= split),
    ]:
        e = events.loc[mask_dates & events[event_col].fillna(False), "date"]
        sub = panel[panel["date"].isin(set(e))]
        for (symbol, name), g in sub.groupby(["symbol", "name"]):
            vals = pd.to_numeric(g[ret_col], errors="coerce").dropna()
            rows.append(
                {
                    "event": event_col,
                    "period": period,
                    "symbol": str(symbol),
                    "name": str(name),
                    "n": int(len(vals)),
                    "mean_excess": float(vals.mean()) if len(vals) else np.nan,
                    "win_rate": float((vals > 0).mean()) if len(vals) else np.nan,
                }
            )
    wide = pd.DataFrame(rows)
    if wide.empty:
        return wide
    is_ = wide[wide.period == "IS"].rename(columns={"n": "n_is", "mean_excess": "mean_is", "win_rate": "win_is"})
    oos = wide[wide.period == "OOS"].rename(columns={"n": "n_oos", "mean_excess": "mean_oos", "win_rate": "win_oos"})
    m = is_.merge(oos, on=["event", "symbol", "name"], how="inner")
    m["same_sign"] = np.sign(m["mean_is"]) == np.sign(m["mean_oos"])
    m["stable"] = (
        m["same_sign"]
        & (m["n_is"] >= 15)
        & (m["n_oos"] >= 10)
        & (m["mean_is"].abs() >= 0.001)
        & (m["mean_oos"].abs() >= 0.001)
    )
    return m


def plot_event_bars(df: pd.DataFrame, event_col: str, title: str, fname: str, top_n: int = 8) -> None:
    if df.empty:
        return
    use = df[df["n"] >= MIN_EVENT_N].copy()
    if use.empty:
        use = df.copy()
    top = use.nlargest(top_n, "mean_excess")
    bot = use.nsmallest(top_n, "mean_excess")
    show = pd.concat([top, bot]).drop_duplicates("symbol")
    show = show.sort_values("mean_excess")
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = ["tab:green" if v >= 0 else "tab:red" for v in show["mean_excess"]]
    ax.barh(show["name"], show["mean_excess"] * 100, color=colors)
    ax.axvline(0, color="black", lw=0.8)
    ax.set_xlabel("Mean excess vs CSI300 (%)")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(CHARTS / fname, dpi=150)
    plt.close(fig)


def write_report(
    events: pd.DataFrame,
    event_tables: dict[str, pd.DataFrame],
    hit_tables: dict[str, pd.DataFrame],
    corr: pd.DataFrame,
    stability: dict[str, pd.DataFrame],
    panel: pd.DataFrame,
) -> Path:
    q80 = events.attrs.get("rt_d5_q80", np.nan)
    q20 = events.attrs.get("rt_d5_q20", np.nan)
    n_dates = panel["date"].nunique()
    date_min, date_max = panel["date"].min(), panel["date"].max()
    n_sec = panel["symbol"].nunique()

    lines = [
        "# 风险温度 × 板块资金偏好：严格事件研究",
        "",
        "## 0. 方法边界（必须先读）",
        "",
        "1. **没有官方「资金流」字段**。本报告用 **板块收益 − 沪深300收益（超额收益）** 作为「相对资金偏好」代理。",
        "2. 超额收益 **不是** 北向/主力净流入；它衡量的是：相对大盘，资本愿意给该板块更高/更低的定价权重。",
        "3. 统计显著性：对事件日超额收益做 **单样本 t 检验（H0: mean=0）**；要求事件样本 **n≥25** 才标「显著候选」。",
        "4. 稳健性：主结论优先 **分位定义事件** 与 **硬阈值事件** 交叉验证；并检查 2024 前后同号稳定。",
        "5. 恒生科技与 A 股交易日不完全重合，已在 RT∩沪深300 交易日上对齐；港股休市日不参与。",
        "",
        f"- 对齐样本区间: **{date_min} → {date_max}**",
        f"- 交易日数: **{n_dates}**",
        f"- 板块数: **{n_sec}**（申万一级 + 恒生科技）",
        f"- ΔRT(5日) 分位阈值: P80≈{_f(q80, 2)}, P20≈{_f(q20, 2)}",
        f"- 硬样本门槛 MIN_EVENT_N={MIN_EVENT_N}, MIN_CORR_N={MIN_CORR_N}",
        "",
        "## 1. 事件定义",
        "",
        "| 事件 | 含义 |",
        "|------|------|",
    ]
    for col, (lab, _) in EVENT_META.items():
        n_evt = int(events[col].fillna(False).sum()) if col in events.columns else 0
        lines.append(f"| `{col}` / {lab} | 事件日数 **{n_evt}** |")

    # Core narrative sections
    def top_bot(df: pd.DataFrame, k: int = 8) -> tuple[pd.DataFrame, pd.DataFrame]:
        use = df[df["n"] >= MIN_EVENT_N] if (df["n"] >= MIN_EVENT_N).any() else df
        return use.nlargest(k, "mean_excess"), use.nsmallest(k, "mean_excess")

    key_events = [
        ("evt_rt_rising_hard", "excess_fwd_5d", "升温时：未来5日相对谁更强（资金倾向流入）/ 谁更弱（相对抽血）"),
        ("evt_rt_rising_q80", "excess_fwd_5d", "升温分位确认"),
        ("evt_rt_falling_hard", "excess_fwd_5d", "降温时：未来5日相对强弱"),
        ("evt_high_cooling", "excess_fwd_5d", "高位回落时：避险相对去向（未来5日超额）"),
        ("evt_high_cooling_strict", "excess_fwd_5d", "高位回落（严格）"),
        ("evt_rt_rising_hard", "excess_1d", "升温当日同步超额（同步定价，非前瞻）"),
        ("evt_high_cooling", "excess_1d", "高位回落当日同步超额"),
    ]

    lines += ["", "## 2. 核心问题回答", ""]

    # Q1 rising
    rise = event_tables.get("evt_rt_rising_hard__excess_fwd_5d", pd.DataFrame())
    if not rise.empty:
        top, bot = top_bot(rise)
        lines += [
            "### Q1. 风险温度升温时，资金相对去哪 / 从哪抽血？",
            "",
            "事件: `ΔRT_5日 ≥ +5`；指标: **事件后 5 日超额收益**（板块 − 沪深300）。",
            "",
            "**相对流入（超额最高，n≥门槛优先）**",
            "",
            "| 板块 | n | 5日均超额 | 胜率 | t | p | 显著 |",
            "|------|--:|----------:|-----:|--:|--:|:----:|",
        ]
        for _, r in top.iterrows():
            lines.append(
                f"| {r['name']} | {int(r['n'])} | {_pct(r['mean_excess'])} | {_pct(r['win_rate'])} | "
                f"{_f(r['t_stat'])} | {_f(r['p_value'], 3)} | {'是' if r['significant_5pct'] else '否'} |"
            )
        lines += [
            "",
            "**相对抽血（超额最低）**",
            "",
            "| 板块 | n | 5日均超额 | 胜率 | t | p | 显著 |",
            "|------|--:|----------:|-----:|--:|--:|:----:|",
        ]
        for _, r in bot.iterrows():
            lines.append(
                f"| {r['name']} | {int(r['n'])} | {_pct(r['mean_excess'])} | {_pct(r['win_rate'])} | "
                f"{_f(r['t_stat'])} | {_f(r['p_value'], 3)} | {'是' if r['significant_5pct'] else '否'} |"
            )

    # Q2 cooling safe haven
    cool = event_tables.get("evt_high_cooling__excess_fwd_5d", pd.DataFrame())
    if not cool.empty:
        top, bot = top_bot(cool)
        lines += [
            "",
            "### Q2. 风险温度高到一定程度、准备降温时，资金相对去哪避险？",
            "",
            "事件: 近10日 RT 峰值≥65，当前 RT≥55，且 ΔRT_5≤−3 且当日下行；指标: 未来5日超额。",
            "",
            "**相对避险赢家（高位回落阶段仍跑赢大盘）**",
            "",
            "| 板块 | n | 5日均超额 | 胜率 | t | p | 显著 |",
            "|------|--:|----------:|-----:|--:|--:|:----:|",
        ]
        for _, r in top.iterrows():
            lines.append(
                f"| {r['name']} | {int(r['n'])} | {_pct(r['mean_excess'])} | {_pct(r['win_rate'])} | "
                f"{_f(r['t_stat'])} | {_f(r['p_value'], 3)} | {'是' if r['significant_5pct'] else '否'} |"
            )
        lines += [
            "",
            "**回落阶段继续受伤（相对最差）**",
            "",
            "| 板块 | n | 5日均超额 | 胜率 | t | p | 显著 |",
            "|------|--:|----------:|-----:|--:|--:|:----:|",
        ]
        for _, r in bot.iterrows():
            lines.append(
                f"| {r['name']} | {int(r['n'])} | {_pct(r['mean_excess'])} | {_pct(r['win_rate'])} | "
                f"{_f(r['t_stat'])} | {_f(r['p_value'], 3)} | {'是' if r['significant_5pct'] else '否'} |"
            )

    # HSTECH focus
    lines += ["", "### Q3. 恒生科技在各事件中的位置", "", "| 事件 | 指标 | n | 均超额 | 胜率 | p |", "|------|------|--:|------:|-----:|--:|"]
    for key, df in event_tables.items():
        if df.empty:
            continue
        row = df[df["symbol"] == "HSTECH"]
        if row.empty:
            continue
        r = row.iloc[0]
        lines.append(
            f"| {r['event_label']} | {r['return_metric']} | {int(r['n'])} | {_pct(r['mean_excess'])} | "
            f"{_pct(r['win_rate'])} | {_f(r['p_value'], 3)} |"
        )

    # Stability
    lines += ["", "## 3. IS/OOS 同号稳定性（2024 前后）", ""]
    for ev, stab in stability.items():
        if stab.empty:
            continue
        stable_in = stab[stab["stable"]].sort_values("mean_oos", ascending=False)
        stable_out = stab[stab["stable"]].sort_values("mean_oos", ascending=True)
        lab = EVENT_META.get(ev, (ev, ""))[0]
        lines += [f"### {lab}", "", f"- 稳定同号板块数: **{int(stab['stable'].sum())}** / {len(stab)}", ""]
        if not stable_in.empty:
            lines.append("**OOS 仍为正超额且稳定的前5**")
            lines.append("")
            lines.append("| 板块 | IS均超额 | OOS均超额 | n_is | n_oos |")
            lines.append("|------|---------:|----------:|-----:|------:|")
            for _, r in stable_in.head(5).iterrows():
                lines.append(
                    f"| {r['name']} | {_pct(r['mean_is'])} | {_pct(r['mean_oos'])} | {int(r['n_is'])} | {int(r['n_oos'])} |"
                )
            lines.append("")
        if not stable_out.empty:
            lines.append("**OOS 仍为负超额且稳定的前5（相对抽血）**")
            lines.append("")
            lines.append("| 板块 | IS均超额 | OOS均超额 | n_is | n_oos |")
            lines.append("|------|---------:|----------:|-----:|------:|")
            for _, r in stable_out.head(5).iterrows():
                lines.append(
                    f"| {r['name']} | {_pct(r['mean_is'])} | {_pct(r['mean_oos'])} | {int(r['n_is'])} | {int(r['n_oos'])} |"
                )
            lines.append("")

    # Correlation appendix
    lines += [
        "## 4. 相关性附录（水平 RT vs 未来超额）",
        "",
        "说明: corr(RT, 未来5日超额)。正相关 ≈ 温度越高，该板块未来相对大盘越强（偏「风险偏好受益」）。",
        "",
    ]
    c5 = corr[corr["metric"] == "corr_rt_excess_fwd5"].dropna(subset=["corr"]).sort_values("corr")
    if not c5.empty:
        lines.append("| 最负相关（温度高→相对弱） | corr | n | p |")
        lines.append("|------|-----:|--:|--:|")
        for _, r in c5.head(8).iterrows():
            lines.append(f"| {r['name']} | {_f(r['corr'])} | {int(r['n'])} | {_f(r['p_value'], 3)} |")
        lines.append("")
        lines.append("| 最正相关（温度高→相对强） | corr | n | p |")
        lines.append("|------|-----:|--:|--:|")
        for _, r in c5.tail(8).iloc[::-1].iterrows():
            lines.append(f"| {r['name']} | {_f(r['corr'])} | {int(r['n'])} | {_f(r['p_value'], 3)} |")

    # Rank hit rates for rising
    if "evt_rt_rising_hard" in hit_tables:
        ht = hit_tables["evt_rt_rising_hard"]
        lines += [
            "",
            "## 5. 升温日「榜单命中率」（Top5/Bottom5 出现频率）",
            "",
            "在升温事件日，按当日或未来超额排序，统计各板块进入前五/后五的频率。",
            "",
        ]
        if not ht.empty:
            lines.append("| 板块 | Top5频率 | Bottom5频率 | 净偏好 |")
            lines.append("|------|---------:|------------:|-------:|")
            for _, r in ht.head(10).iterrows():
                lines.append(
                    f"| {r['name']} | {_pct(r['top_k_rate'])} | {_pct(r['bottom_k_rate'])} | {_f(r['net_preference'], 3)} |"
                )
            lines.append("")
            lines.append("净偏好最低（常居后五）:")
            lines.append("")
            lines.append("| 板块 | Top5频率 | Bottom5频率 | 净偏好 |")
            lines.append("|------|---------:|------------:|-------:|")
            for _, r in ht.tail(10).iloc[::-1].iterrows():
                lines.append(
                    f"| {r['name']} | {_pct(r['top_k_rate'])} | {_pct(r['bottom_k_rate'])} | {_f(r['net_preference'], 3)} |"
                )

    lines += [
        "",
        "## 6. 综合结论（严格口径）",
        "",
        "1. **升温阶段**：优先看「未来5日超额最高且 n 达标、最好 IS/OOS 同号」的板块作为相对流入候选；最低者为相对抽血候选。",
        "2. **高位降温/避险**：优先看 high_cooling 事件下超额最高的板块（往往偏防御或高股息属性，以数据为准）。",
        "3. **恒生科技**：单独列表；若 n 不足或港股错日较多，结论强度自动降级。",
        "4. **禁止过度解读**：单次显著、样本不足、或仅 IS 显著 OOS 翻号的结果，不得写成确定资金规律。",
        "",
        "## 7. 输出文件",
        "",
        "- `research/output/sector_flow/event_sector_stats.csv`",
        "- `research/output/sector_flow/rank_hit_rates.csv`",
        "- `research/output/sector_flow/correlations.csv`",
        "- `research/output/sector_flow/stability_*.csv`",
        "- `research/output/sector_flow/charts/`",
        "",
    ]
    path = OUT / "strict_rt_sector_flow_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    warnings.filterwarnings("ignore", category=FutureWarning)
    ensure_dirs()
    print("Loading base data...")
    risk, hs, sectors = load_base()
    print(f"RT {risk['date'].min()}→{risk['date'].max()} n={len(risk)}")
    print(f"Sectors symbols={sectors['symbol'].nunique()}")

    print("Building panel...")
    panel = build_panel(risk, hs, sectors)
    panel.to_csv(OUT / "panel_sample_head.csv", index=False)  # full panel is huge; save meta only
    meta = {
        "dates": int(panel["date"].nunique()),
        "sectors": int(panel["symbol"].nunique()),
        "rows": int(len(panel)),
        "start": panel["date"].min(),
        "end": panel["date"].max(),
        "hstech_rows": int((panel["symbol"] == "HSTECH").sum()),
    }
    pd.Series(meta).to_json(OUT / "panel_meta.json", force_ascii=False, indent=2)
    print("Panel", meta)

    print("Defining events...")
    events = define_events(risk)
    event_count = {c: int(events[c].fillna(False).sum()) for c in events.columns if c.startswith("evt_")}
    pd.Series(event_count).to_csv(OUT / "event_counts.csv", header=["count"])
    print("Event counts", event_count)

    # Compute all event x return metric tables
    event_cols = [c for c in events.columns if c.startswith("evt_")]
    ret_cols = ["excess_1d", "excess_fwd_5d", "excess_fwd_10d", "excess_fwd_20d"]
    all_stats = []
    event_tables: dict[str, pd.DataFrame] = {}
    print("Event stats...")
    for ev in event_cols:
        for rc in ret_cols:
            st = event_stats(panel, events, ev, rc)
            if st.empty:
                continue
            key = f"{ev}__{rc}"
            event_tables[key] = st
            all_stats.append(st)
            if rc == "excess_fwd_5d" and ev in {
                "evt_rt_rising_hard",
                "evt_rt_falling_hard",
                "evt_high_cooling",
                "evt_high_cooling_strict",
                "evt_rt_rising_q80",
            }:
                plot_event_bars(
                    st,
                    ev,
                    title=f"{EVENT_META.get(ev, (ev,))[0]} | {rc}",
                    fname=f"{ev}_{rc}.png",
                )
    stats_df = pd.concat(all_stats, ignore_index=True) if all_stats else pd.DataFrame()
    stats_df.to_csv(OUT / "event_sector_stats.csv", index=False)
    print(f"Stats rows={len(stats_df)}")

    print("Rank hit rates...")
    hit_tables = {}
    hit_all = []
    for ev in ["evt_rt_rising_hard", "evt_rt_falling_hard", "evt_high_cooling", "evt_rt_rising_q80"]:
        for rc in ["excess_1d", "excess_fwd_5d"]:
            ht = rank_hit_rate(panel, events, ev, rc, top_k=5)
            if ht.empty:
                continue
            ht["return_metric"] = rc
            hit_tables[ev if rc == "excess_fwd_5d" else f"{ev}_{rc}"] = ht
            hit_all.append(ht.assign(return_metric=rc))
    if hit_all:
        pd.concat(hit_all, ignore_index=True).to_csv(OUT / "rank_hit_rates.csv", index=False)

    print("Correlations...")
    corr = correlation_table(panel)
    corr.to_csv(OUT / "correlations.csv", index=False)

    print("IS/OOS stability...")
    stability = {}
    for ev in ["evt_rt_rising_hard", "evt_rt_falling_hard", "evt_high_cooling", "evt_rt_rising_q80", "evt_high_cooling_strict"]:
        stab = oos_stability(panel, events, ev, "excess_fwd_5d")
        stability[ev] = stab
        if not stab.empty:
            stab.to_csv(OUT / f"stability_{ev}.csv", index=False)

    report = write_report(events, event_tables, hit_tables, corr, stability, panel)
    print("Report:", report)

    # Console summary
    for key in [
        "evt_rt_rising_hard__excess_fwd_5d",
        "evt_high_cooling__excess_fwd_5d",
        "evt_rt_falling_hard__excess_fwd_5d",
    ]:
        df = event_tables.get(key, pd.DataFrame())
        if df.empty:
            continue
        use = df[df.n >= MIN_EVENT_N] if (df.n >= MIN_EVENT_N).any() else df
        print("\n==", key, "==")
        print("TOP", use.nlargest(5, "mean_excess")[["name", "n", "mean_excess", "win_rate", "p_value"]].to_string(index=False))
        print("BOT", use.nsmallest(5, "mean_excess")[["name", "n", "mean_excess", "win_rate", "p_value"]].to_string(index=False))


if __name__ == "__main__":
    main()
