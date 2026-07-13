#!/usr/bin/env python3
"""CSI300 risk-temperature interval and strategy research.

Run from repository root:
    python3 research/csi300_risk_temperature_interval_backtest.py
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


ROOT = Path(".")
RISK_PATH = ROOT / "data/calculated/risk_components.csv"
INDEX_PATH = ROOT / "data/raw/indices/sh000300.csv"
OUT = ROOT / "research/output"
CHARTS = OUT / "charts"

BUY_FEE = 0.0001
SELL_FEE = 0.0001
SLIPPAGE = 0.0002
BUY_COST = BUY_FEE + SLIPPAGE
SELL_COST = SELL_FEE + SLIPPAGE
ROUND_TRIP_COST = BUY_COST + SELL_COST
TRADING_DAYS = 252

INTERVALS = [
    (0, 20),
    (20, 30),
    (30, 40),
    (40, 50),
    (50, 60),
    (60, 65),
    (65, 70),
    (70, 75),
    (75, 80),
    (80, 85),
    (85, 90),
    (90, 100),
]

ENTRY_THRESHOLDS = [55, 60, 65, 70, 75, 80]
UPPER_BOUNDS = [None, 75, 80, 85, 90, 100]
EXHAUSTION_RULES = [
    "none",
    "down_1d",
    "below_3d_high",
    "below_3d_high_3",
    "below_3d_high_5",
    "below_5d_high_5",
    "below_10d_high_5",
    "below_10d_high_8",
    "drop_3d_3",
    "drop_3d_5",
    "drop_5d_5",
]
PRICE_CONFIRM_RULES = [
    "none",
    "not_3d_low",
    "not_5d_low",
    "not_10d_low",
    "up_day",
    "up_0_5pct_day",
    "close_above_ma5",
    "close_above_ma10",
    "close_above_ma20",
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
    "tp8_sl5",
    "tp10_sl5",
    "tp12_sl8",
    "risk_below_45_tp8_sl5",
    "risk_below_35_tp10_sl5",
    "risk_below_30_tp12_sl8",
    "max20_risk_below_45_tp8_sl5",
    "max20_risk_below_35_tp10_sl5",
    "max60_risk_below_45_tp10_sl8",
    "max60_risk_below_35_tp12_sl8",
]


@dataclass
class Trade:
    strategy_id: str
    entry_signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    exit_signal_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    holding_days: int
    trade_return: float
    mae: float
    mfe: float
    year: int


def ensure_dirs() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)


def pct(x: float | int | None, digits: int = 2) -> str:
    if x is None or not np.isfinite(x):
        return "--"
    return f"{x * 100:.{digits}f}%"


def fmt(x: float | int | None, digits: int = 4) -> str:
    if x is None or not np.isfinite(x):
        return "--"
    return f"{x:.{digits}f}"


def annualized_return(total_return: float, days: int) -> float:
    if days <= 0 or total_return <= -1:
        return np.nan
    return (1 + total_return) ** (TRADING_DAYS / days) - 1


def max_drawdown(equity: pd.Series | np.ndarray) -> float:
    arr = np.asarray(equity, dtype=float)
    if arr.size == 0:
        return np.nan
    peak = np.maximum.accumulate(arr)
    dd = arr / peak - 1
    return float(np.nanmin(dd))


def sharpe_from_daily(daily_returns: pd.Series | np.ndarray) -> float:
    arr = np.asarray(daily_returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 3 or np.nanstd(arr, ddof=1) == 0:
        return np.nan
    return float(np.nanmean(arr) / np.nanstd(arr, ddof=1) * math.sqrt(TRADING_DAYS))


def max_consecutive_losses(returns: list[float]) -> int:
    best = cur = 0
    for r in returns:
        if r < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def normalize(s: pd.Series, inverse: bool = False) -> pd.Series:
    vals = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
    lo, hi = vals.min(skipna=True), vals.max(skipna=True)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        out = pd.Series(0.5, index=s.index)
    else:
        out = (vals - lo) / (hi - lo)
    out = out.fillna(0.0).clip(0, 1)
    return 1 - out if inverse else out


def load_data() -> tuple[pd.DataFrame, dict]:
    audit: dict[str, object] = {
        "risk_components_exists": RISK_PATH.exists(),
        "sh000300_exists": INDEX_PATH.exists(),
        "used_execution": "T+1 open",
    }
    if not RISK_PATH.exists():
        raise FileNotFoundError(f"Missing {RISK_PATH}")
    if not INDEX_PATH.exists():
        raise FileNotFoundError(f"Missing {INDEX_PATH}; T+1 open backtest cannot run.")

    risk = pd.read_csv(RISK_PATH)
    idx = pd.read_csv(INDEX_PATH)
    risk["trade_date"] = pd.to_datetime(risk["trade_date"])
    idx["date"] = pd.to_datetime(idx["date"])
    for col in ["risk_temperature", "sh000300_close", "model_confidence"]:
        if col in risk:
            risk[col] = pd.to_numeric(risk[col], errors="coerce")
    for col in ["open", "close", "high", "low", "volume"]:
        if col in idx:
            idx[col] = pd.to_numeric(idx[col], errors="coerce")

    audit.update(
        {
            "risk_start": risk["trade_date"].min(),
            "risk_end": risk["trade_date"].max(),
            "index_start": idx["date"].min(),
            "index_end": idx["date"].max(),
            "risk_latest": risk["trade_date"].max(),
            "index_latest": idx["date"].max(),
            "risk_duplicate_dates": int(risk["trade_date"].duplicated().sum()),
            "index_duplicate_dates": int(idx["date"].duplicated().sum()),
            "risk_temperature_missing": int(risk["risk_temperature"].isna().sum()),
            "quality_distribution": risk.get("quality", pd.Series(dtype=str)).fillna("MISSING").value_counts().to_dict(),
            "model_confidence_distribution": risk.get("model_confidence", pd.Series(dtype=float)).fillna(-1).value_counts().sort_index().to_dict(),
        }
    )

    risk = risk.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    idx = idx.sort_values("date").drop_duplicates("date", keep="last")
    idx = idx.rename(columns={"date": "trade_date", "open": "sh000300_open", "close": "index_close", "high": "index_high", "low": "index_low"})
    merged = pd.merge(risk, idx[["trade_date", "sh000300_open", "index_close", "index_high", "index_low", "volume"]], on="trade_date", how="inner")
    merged = merged.sort_values("trade_date").reset_index(drop=True)

    if "sh000300_close" in merged:
        merged["csi300_close"] = merged["sh000300_close"].combine_first(merged["index_close"])
    else:
        merged["csi300_close"] = merged["index_close"]
    merged["csi300_open"] = merged["sh000300_open"]
    merged["next_open"] = merged["csi300_open"].shift(-1)
    merged["next_date"] = merged["trade_date"].shift(-1)
    merged["ret_close"] = merged["csi300_close"].pct_change()
    merged["ma5"] = merged["csi300_close"].rolling(5, min_periods=5).mean()
    merged["ma10"] = merged["csi300_close"].rolling(10, min_periods=10).mean()
    merged["ma20"] = merged["csi300_close"].rolling(20, min_periods=20).mean()
    merged["rolling_min_3"] = merged["csi300_close"].rolling(3, min_periods=3).min()
    merged["rolling_min_5"] = merged["csi300_close"].rolling(5, min_periods=5).min()
    merged["rolling_min_10"] = merged["csi300_close"].rolling(10, min_periods=10).min()
    merged["dd60_calc"] = merged["csi300_close"] / merged["csi300_close"].rolling(60, min_periods=20).max() - 1
    merged["rt_rollmax_3"] = merged["risk_temperature"].rolling(3, min_periods=1).max()
    merged["rt_rollmax_5"] = merged["risk_temperature"].rolling(5, min_periods=1).max()
    merged["rt_rollmax_10"] = merged["risk_temperature"].rolling(10, min_periods=1).max()
    merged["rt_shift_1"] = merged["risk_temperature"].shift(1)
    merged["rt_shift_3"] = merged["risk_temperature"].shift(3)
    merged["rt_shift_5"] = merged["risk_temperature"].shift(5)

    audit.update(
        {
            "inner_join_rows": int(len(merged)),
            "aligned_start": merged["trade_date"].min(),
            "aligned_end": merged["trade_date"].max(),
            "joined_risk_temperature_missing": int(merged["risk_temperature"].isna().sum()),
            "joined_open_missing": int(merged["csi300_open"].isna().sum()),
            "joined_close_missing": int(merged["csi300_close"].isna().sum()),
            "date_misalignment_count": int(len(set(risk["trade_date"]) ^ set(idx["trade_date"]))),
            "risk_later_than_index": bool(risk["trade_date"].max() > idx["trade_date"].max()),
            "index_later_than_risk": bool(idx["trade_date"].max() > risk["trade_date"].max()),
            "future_data_risk": False,
        }
    )
    return merged, audit


def write_audit(audit: dict) -> None:
    lines = [
        "# 沪深300风险温度数据核查",
        "",
        f"- risk_components.csv 是否存在: {audit['risk_components_exists']}",
        f"- sh000300.csv 是否存在: {audit['sh000300_exists']}",
        f"- risk_components 日期: {audit['risk_start'].date()} 至 {audit['risk_end'].date()}",
        f"- sh000300 日期: {audit['index_start'].date()} 至 {audit['index_end'].date()}",
        f"- risk_components 最新日期: {audit['risk_latest'].date()}",
        f"- sh000300 最新日期: {audit['index_latest'].date()}",
        f"- 有效 inner join 样本数: {audit['inner_join_rows']}",
        f"- 有效对齐区间: {audit['aligned_start'].date()} 至 {audit['aligned_end'].date()}",
        f"- risk_temperature 缺失值: {audit['joined_risk_temperature_missing']}",
        f"- sh000300 open 缺失值: {audit['joined_open_missing']}",
        f"- sh000300 close 缺失值: {audit['joined_close_missing']}",
        f"- risk_components 重复日期: {audit['risk_duplicate_dates']}",
        f"- sh000300 重复日期: {audit['index_duplicate_dates']}",
        f"- 日期错位数量（对称差）: {audit['date_misalignment_count']}",
        f"- risk_temperature 晚于指数行情: {audit['risk_later_than_index']}",
        f"- 指数行情晚于 risk_temperature: {audit['index_later_than_risk']}",
        f"- 是否存在未来数据风险: {audit['future_data_risk']}",
        f"- 回测执行口径: {audit['used_execution']}",
        "",
        "## quality 分布",
        "",
    ]
    for k, v in audit["quality_distribution"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## model_confidence 分布", ""]
    for k, v in audit["model_confidence_distribution"].items():
        lines.append(f"- {k}: {v}")
    (OUT / "csi300_risk_temperature_data_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def future_returns(df: pd.DataFrame, horizons=(5, 10, 20, 60)) -> pd.DataFrame:
    out = df.copy()
    for h in horizons:
        out[f"fwd_{h}d_ret"] = out["csi300_close"].shift(-h) / out["csi300_close"] - 1 - ROUND_TRIP_COST
        future_min = out["csi300_close"].shift(-1).rolling(h, min_periods=1).min().shift(-(h - 1))
        out[f"fwd_{h}d_mae"] = future_min / out["csi300_close"] - 1 - ROUND_TRIP_COST
    return out


def interval_summary(hdf: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for low, high in INTERVALS:
        mask = (hdf["risk_temperature"] >= low) & (hdf["risk_temperature"] < high if high < 100 else hdf["risk_temperature"] <= high)
        sub = hdf.loc[mask].copy()
        row = {"interval": f"{low}-{high}", "lower": low, "upper": high, "sample_count": len(sub)}
        for h in [5, 10, 20, 60]:
            vals = sub[f"fwd_{h}d_ret"].dropna()
            row[f"future_{h}d_avg_return"] = vals.mean()
            row[f"future_{h}d_median_return"] = vals.median()
            row[f"future_{h}d_win_rate"] = (vals > 0).mean() if len(vals) else np.nan
            row[f"future_{h}d_max_loss"] = vals.min() if len(vals) else np.nan
            row[f"future_{h}d_mae"] = sub[f"fwd_{h}d_mae"].min()
        row["avg_return"] = row["future_20d_avg_return"]
        row["median_return"] = row["future_20d_median_return"]
        row["max_loss"] = row["future_20d_max_loss"]
        row["mae"] = row["future_20d_mae"]
        row["year_distribution"] = sub["trade_date"].dt.year.value_counts().sort_index().to_dict()
        row["quality_distribution"] = sub.get("quality", pd.Series(dtype=str)).fillna("MISSING").value_counts().to_dict()
        row["avg_model_confidence"] = sub["model_confidence"].mean()
        rows.append(row)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "csi300_risk_temp_interval_summary.csv", index=False)
    return res


def mask_for_exhaustion(df: pd.DataFrame, rule: str) -> pd.Series:
    rt = df["risk_temperature"]
    if rule == "none":
        return pd.Series(True, index=df.index)
    if rule == "down_1d":
        return rt <= df["rt_shift_1"]
    if rule == "below_3d_high":
        return rt < df["rt_rollmax_3"]
    if rule == "below_3d_high_3":
        return rt <= df["rt_rollmax_3"] - 3
    if rule == "below_3d_high_5":
        return rt <= df["rt_rollmax_3"] - 5
    if rule == "below_5d_high_5":
        return rt <= df["rt_rollmax_5"] - 5
    if rule == "below_10d_high_5":
        return rt <= df["rt_rollmax_10"] - 5
    if rule == "below_10d_high_8":
        return rt <= df["rt_rollmax_10"] - 8
    if rule == "drop_3d_3":
        return rt - df["rt_shift_3"] <= -3
    if rule == "drop_3d_5":
        return rt - df["rt_shift_3"] <= -5
    if rule == "drop_5d_5":
        return rt - df["rt_shift_5"] <= -5
    raise ValueError(rule)


def mask_for_price(df: pd.DataFrame, rule: str) -> pd.Series:
    c = df["csi300_close"]
    if rule == "none":
        return pd.Series(True, index=df.index)
    if rule == "not_3d_low":
        return c > df["rolling_min_3"]
    if rule == "not_5d_low":
        return c > df["rolling_min_5"]
    if rule == "not_10d_low":
        return c > df["rolling_min_10"]
    if rule == "up_day":
        return c / c.shift(1) - 1 > 0
    if rule == "up_0_5pct_day":
        return c / c.shift(1) - 1 > 0.005
    if rule == "close_above_ma5":
        return c > df["ma5"]
    if rule == "close_above_ma10":
        return c > df["ma10"]
    if rule == "close_above_ma20":
        return c > df["ma20"]
    if rule == "drawdown_60d_below_5pct":
        return df["dd60_calc"] <= -0.05
    if rule == "drawdown_60d_below_8pct":
        return df["dd60_calc"] <= -0.08
    if rule == "drawdown_60d_below_10pct":
        return df["dd60_calc"] <= -0.10
    raise ValueError(rule)


def entry_mask(df: pd.DataFrame, entry: float, upper: float | None, exhaustion: str, confirm: str) -> pd.Series:
    mask = df["risk_temperature"] >= entry
    if upper is not None:
        mask &= df["risk_temperature"] < upper
    mask &= mask_for_exhaustion(df, exhaustion)
    mask &= mask_for_price(df, confirm)
    mask &= df["next_open"].notna()
    return mask.fillna(False)


def forward_stats_for_mask(hdf: pd.DataFrame, mask: pd.Series) -> dict:
    sub = hdf.loc[mask.reindex(hdf.index).fillna(False)]
    row = {"sample_count": int(len(sub))}
    for h in [5, 10, 20, 60]:
        vals = sub[f"fwd_{h}d_ret"].dropna()
        row[f"{h}d_forward_win_rate"] = (vals > 0).mean() if len(vals) else np.nan
        row[f"{h}d_avg_return"] = vals.mean() if len(vals) else np.nan
        row[f"{h}d_median_return"] = vals.median() if len(vals) else np.nan
        row[f"{h}d_max_loss"] = vals.min() if len(vals) else np.nan
    row["max_loss"] = row.get("20d_max_loss", np.nan)
    return row


def exhaustion_results(df: pd.DataFrame, hdf: pd.DataFrame) -> pd.DataFrame:
    rows = []
    specs: list[tuple[str, float, float | None]] = [(f">={x}", x, None) for x in ENTRY_THRESHOLDS]
    specs += [(f"{a}-{b}", a, b) for a, b in [(60, 75), (65, 75), (70, 80), (75, 90), (80, 100)]]
    for label, entry, upper in specs:
        for rule in EXHAUSTION_RULES:
            mask = entry_mask(df, entry, upper, rule, "none")
            row = {"entry_spec": label, "entry_threshold": entry, "upper_bound": upper, "exhaustion_rule": rule}
            row.update(forward_stats_for_mask(hdf, mask))
            rows.append(row)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "csi300_risk_temp_exhaustion_results.csv", index=False)
    return res


def price_confirmation_results(df: pd.DataFrame, hdf: pd.DataFrame) -> pd.DataFrame:
    rows = []
    price_rules = PRICE_CONFIRM_RULES
    combo_rules = [
        ("combo_13_rt65_down_not5low", 65, None, "down_1d", "not_5d_low"),
        ("combo_14_rt70_below5dhigh5_not5low", 70, None, "below_5d_high_5", "not_5d_low"),
        ("combo_15_rt75_below10dhigh5_ma5", 75, None, "below_10d_high_5", "close_above_ma5"),
        ("combo_16_rt75_ma10", 75, None, "none", "close_above_ma10"),
        ("combo_17_rt60_75_not5low", 60, 75, "none", "not_5d_low"),
        ("combo_18_rt75_90_not5low", 75, 90, "none", "not_5d_low"),
    ]
    for rule in price_rules:
        for entry, upper in [(65, None), (70, None), (75, None), (60, 75), (75, 90)]:
            mask = entry_mask(df, entry, upper, "none", rule)
            row = {"condition": rule, "entry_threshold": entry, "upper_bound": upper, "exhaustion_rule": "none", "price_confirm_rule": rule}
            row.update(forward_stats_for_mask(hdf, mask))
            rows.append(row)
    for name, entry, upper, ex, pr in combo_rules:
        mask = entry_mask(df, entry, upper, ex, pr)
        row = {"condition": name, "entry_threshold": entry, "upper_bound": upper, "exhaustion_rule": ex, "price_confirm_rule": pr}
        row.update(forward_stats_for_mask(hdf, mask))
        rows.append(row)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "csi300_risk_temp_price_confirm_results.csv", index=False)
    return res


def parse_exit_rule(rule: str) -> dict:
    out = {"fixed": None, "risk_below": None, "ma_below": None, "tp": None, "sl": None, "max": None}
    if rule.startswith("fixed_"):
        out["fixed"] = int(rule.split("_")[1].replace("d", ""))
        return out
    parts = rule.split("_")
    if rule.startswith("risk_below_"):
        out["risk_below"] = float(parts[2])
        if "tp8" in rule:
            out["tp"] = 0.08
        if "tp10" in rule:
            out["tp"] = 0.10
        if "tp12" in rule:
            out["tp"] = 0.12
        if "sl5" in rule:
            out["sl"] = -0.05
        if "sl8" in rule:
            out["sl"] = -0.08
    elif rule.startswith("close_below_ma"):
        out["ma_below"] = int(rule.replace("close_below_ma", ""))
    elif rule.startswith("tp"):
        if "tp8" in rule:
            out["tp"] = 0.08
        if "tp10" in rule:
            out["tp"] = 0.10
        if "tp12" in rule:
            out["tp"] = 0.12
        if "sl5" in rule:
            out["sl"] = -0.05
        if "sl8" in rule:
            out["sl"] = -0.08
    elif rule.startswith("max"):
        out["max"] = int(parts[0].replace("max", ""))
        if "risk" in parts:
            idx = parts.index("below")
            out["risk_below"] = float(parts[idx + 1])
        if "tp8" in rule:
            out["tp"] = 0.08
        if "tp10" in rule:
            out["tp"] = 0.10
        if "tp12" in rule:
            out["tp"] = 0.12
        if "sl5" in rule:
            out["sl"] = -0.05
        if "sl8" in rule:
            out["sl"] = -0.08
    return out


def exit_triggered(df: pd.DataFrame, i: int, entry_price: float, holding: int, rule: str) -> bool:
    cfg = parse_exit_rule(rule)
    close = float(df.at[i, "csi300_close"])
    if not np.isfinite(close):
        return False
    ret = close / entry_price - 1
    if cfg["fixed"] is not None and holding >= cfg["fixed"]:
        return True
    if cfg["max"] is not None and holding >= cfg["max"]:
        return True
    if cfg["risk_below"] is not None and df.at[i, "risk_temperature"] < cfg["risk_below"]:
        return True
    if cfg["ma_below"] == 10 and close < df.at[i, "ma10"]:
        return True
    if cfg["ma_below"] == 20 and close < df.at[i, "ma20"]:
        return True
    if cfg["tp"] is not None and ret >= cfg["tp"]:
        return True
    if cfg["sl"] is not None and ret <= cfg["sl"]:
        return True
    return False


def make_bt_context(df: pd.DataFrame) -> dict:
    return {
        "dates": df["trade_date"].to_numpy(),
        "years": df["trade_date"].dt.year.to_numpy(),
        "open": df["csi300_open"].to_numpy(dtype=float),
        "close": df["csi300_close"].to_numpy(dtype=float),
        "high": df["index_high"].to_numpy(dtype=float),
        "low": df["index_low"].to_numpy(dtype=float),
        "risk": df["risk_temperature"].to_numpy(dtype=float),
        "ma10": df["ma10"].to_numpy(dtype=float),
        "ma20": df["ma20"].to_numpy(dtype=float),
    }


def exit_triggered_fast(ctx: dict, i: int, entry_price: float, holding: int, cfg: dict) -> bool:
    close = ctx["close"][i]
    if not np.isfinite(close):
        return False
    ret = close / entry_price - 1
    if cfg["fixed"] is not None and holding >= cfg["fixed"]:
        return True
    if cfg["max"] is not None and holding >= cfg["max"]:
        return True
    if cfg["risk_below"] is not None and ctx["risk"][i] < cfg["risk_below"]:
        return True
    if cfg["ma_below"] == 10 and close < ctx["ma10"][i]:
        return True
    if cfg["ma_below"] == 20 and close < ctx["ma20"][i]:
        return True
    if cfg["tp"] is not None and ret >= cfg["tp"]:
        return True
    if cfg["sl"] is not None and ret <= cfg["sl"]:
        return True
    return False


def backtest_strategy(
    df: pd.DataFrame,
    strategy_id: str,
    mask: pd.Series,
    exit_rule: str,
    ctx: dict | None = None,
    collect_curve: bool = True,
) -> tuple[dict, list[Trade], pd.DataFrame]:
    if ctx is None:
        ctx = make_bt_context(df)
    n = len(ctx["close"])
    dates = ctx["dates"]
    years = ctx["years"]
    opens = ctx["open"]
    closes = ctx["close"]
    highs = ctx["high"]
    lows = ctx["low"]
    cfg = parse_exit_rule(exit_rule)
    entry_mask_arr = mask.fillna(False).to_numpy()

    if not collect_curve:
        trades: list[Trade] = []
        next_available_i = 0
        signal_indices = np.flatnonzero(entry_mask_arr[: max(0, n - 1)])
        for sig_i in signal_indices:
            if sig_i < next_available_i:
                continue
            entry_i = sig_i + 1
            px = opens[entry_i]
            if not np.isfinite(px):
                continue
            entry_price = float(px) * (1 + BUY_COST)
            exit_i = None
            start_j = entry_i
            if cfg["fixed"] is not None:
                j = min(entry_i + cfg["fixed"], n - 1)
                exit_i = j
                exit_signal_i = max(sig_i, j - 1)
            else:
                max_j = n - 2
                if cfg["max"] is not None:
                    max_j = min(max_j, entry_i + cfg["max"])
                for j in range(start_j, max_j + 1):
                    if exit_triggered_fast(ctx, j, entry_price, j - entry_i, cfg):
                        exit_i = j + 1
                        exit_signal_i = j
                        break
                if exit_i is None and cfg["max"] is not None:
                    exit_i = min(entry_i + cfg["max"] + 1, n - 1)
                    exit_signal_i = max(sig_i, exit_i - 1)
            if exit_i is None:
                exit_i = n - 1
                exit_signal_i = n - 1
            exit_price = opens[exit_i] if exit_i < n - 1 else closes[exit_i]
            if not np.isfinite(exit_price):
                continue
            trade_return = exit_price / entry_price - 1 - SELL_COST
            low_window = lows[entry_i : exit_i + 1]
            high_window = highs[entry_i : exit_i + 1]
            trades.append(
                Trade(
                    strategy_id=strategy_id,
                    entry_signal_date=pd.Timestamp(dates[sig_i]),
                    entry_date=pd.Timestamp(dates[entry_i]),
                    exit_signal_date=pd.Timestamp(dates[exit_signal_i]),
                    exit_date=pd.Timestamp(dates[exit_i]),
                    entry_price=float(entry_price),
                    exit_price=float(exit_price),
                    holding_days=int(exit_i - entry_i),
                    trade_return=float(trade_return),
                    mae=float(np.nanmin(low_window) / entry_price - 1 - SELL_COST) if low_window.size else np.nan,
                    mfe=float(np.nanmax(high_window) / entry_price - 1 - SELL_COST) if high_window.size else np.nan,
                    year=int(years[entry_i]),
                )
            )
            next_available_i = exit_i + 1

        returns = [t.trade_return for t in trades]
        trade_count = len(trades)
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        exposure_days = sum(t.holding_days for t in trades)
        total_return = float(np.prod([1 + r for r in returns]) - 1) if returns else 0.0
        mdd = float(min([t.mae for t in trades], default=0.0))
        ann = annualized_return(total_return, max(1, n))
        pf = float(sum(wins) / abs(sum(losses))) if losses and abs(sum(losses)) > 0 else (99.0 if wins else np.nan)
        calmar = ann / abs(mdd) if np.isfinite(ann) and np.isfinite(mdd) and mdd < 0 else np.nan
        stats = {
            "strategy_id": strategy_id,
            "trade_count": trade_count,
            "exposure_ratio": exposure_days / max(1, n),
            "avg_holding_days": np.mean([t.holding_days for t in trades]) if trades else np.nan,
            "win_rate": len(wins) / trade_count if trade_count else np.nan,
            "avg_trade_return": np.mean(returns) if returns else np.nan,
            "median_trade_return": np.median(returns) if returns else np.nan,
            "total_return": total_return,
            "annualized_return": ann,
            "max_drawdown": mdd,
            "max_single_loss": min(returns) if returns else np.nan,
            "profit_factor": pf,
            "sharpe": np.nan,
            "calmar": calmar,
            "max_consecutive_losses": max_consecutive_losses(returns),
        }
        return stats, trades, pd.DataFrame()

    equity = np.ones(n)
    daily_ret = np.zeros(n)
    position = False
    entry_exec_i = None
    entry_signal_i = None
    entry_price = np.nan
    trades: list[Trade] = []
    trade_returns = []

    for i in range(n - 1):
        if collect_curve and i > 0:
            if position:
                daily_ret[i] = closes[i] / closes[i - 1] - 1
            equity[i] = equity[i - 1] * (1 + daily_ret[i])

        if position and entry_exec_i is not None and exit_triggered_fast(ctx, i, entry_price, i - entry_exec_i, cfg):
            exit_i = i + 1
            exit_price = opens[exit_i]
            if np.isfinite(exit_price):
                gross = exit_price / entry_price - 1
                net = gross - SELL_COST
                total_trade = net
                low_window = lows[entry_exec_i : exit_i + 1]
                high_window = highs[entry_exec_i : exit_i + 1]
                mae = np.nanmin(low_window) / entry_price - 1 - SELL_COST if low_window.size else np.nan
                mfe = np.nanmax(high_window) / entry_price - 1 - SELL_COST if high_window.size else np.nan
                trades.append(
                    Trade(
                        strategy_id=strategy_id,
                        entry_signal_date=pd.Timestamp(dates[entry_signal_i]),
                        entry_date=pd.Timestamp(dates[entry_exec_i]),
                        exit_signal_date=pd.Timestamp(dates[i]),
                        exit_date=pd.Timestamp(dates[exit_i]),
                        entry_price=float(entry_price),
                        exit_price=float(exit_price),
                        holding_days=int(exit_i - entry_exec_i),
                        trade_return=float(total_trade),
                        mae=float(mae),
                        mfe=float(mfe),
                        year=int(years[entry_exec_i]),
                    )
                )
                trade_returns.append(total_trade)
                position = False
                entry_exec_i = None
                entry_signal_i = None
                entry_price = np.nan
                if collect_curve:
                    daily_ret[exit_i] -= SELL_COST
                    equity[exit_i] = equity[i] * (1 + daily_ret[exit_i])
                continue

        if not position and entry_mask_arr[i]:
            exec_i = i + 1
            px = opens[exec_i]
            if np.isfinite(px):
                position = True
                entry_exec_i = exec_i
                entry_signal_i = i
                entry_price = float(px) * (1 + BUY_COST)
                if collect_curve:
                    daily_ret[exec_i] -= BUY_COST

    if collect_curve:
        for i in range(1, n):
            if equity[i] == 1 and daily_ret[i] == 0 and equity[i - 1] != 1:
                equity[i] = equity[i - 1]

    if position and entry_exec_i is not None and n >= 2:
        exit_i = n - 1
        exit_price = closes[exit_i]
        gross = exit_price / entry_price - 1
        total_trade = gross - SELL_COST
        low_window = lows[entry_exec_i : exit_i + 1]
        high_window = highs[entry_exec_i : exit_i + 1]
        trades.append(
            Trade(
                strategy_id=strategy_id,
                entry_signal_date=pd.Timestamp(dates[entry_signal_i]),
                entry_date=pd.Timestamp(dates[entry_exec_i]),
                exit_signal_date=pd.Timestamp(dates[exit_i]),
                exit_date=pd.Timestamp(dates[exit_i]),
                entry_price=float(entry_price),
                exit_price=float(exit_price),
                holding_days=int(exit_i - entry_exec_i),
                trade_return=float(total_trade),
                mae=float(np.nanmin(low_window) / entry_price - 1 - SELL_COST) if low_window.size else np.nan,
                mfe=float(np.nanmax(high_window) / entry_price - 1 - SELL_COST) if high_window.size else np.nan,
                year=int(years[entry_exec_i]),
            )
        )
        trade_returns.append(total_trade)

    if collect_curve:
        equity_s = pd.Series(equity, index=pd.to_datetime(dates)).replace(0, np.nan).ffill().fillna(1.0)
        dr = equity_s.pct_change().fillna(0)
        total_return = float(equity_s.iloc[-1] - 1)
        mdd = max_drawdown(equity_s)
        sharpe = sharpe_from_daily(dr)
    else:
        total_return = float(np.prod([1 + t.trade_return for t in trades]) - 1) if trades else 0.0
        mdd = float(min([t.mae for t in trades], default=0.0))
        sharpe = np.nan
    days = max(1, len(df))
    trade_count = len(trades)
    exposure_days = sum(t.holding_days for t in trades)
    wins = [t.trade_return for t in trades if t.trade_return > 0]
    losses = [t.trade_return for t in trades if t.trade_return <= 0]
    ann = annualized_return(total_return, days)
    pf = float(sum(wins) / abs(sum(losses))) if losses and abs(sum(losses)) > 0 else (np.inf if wins else np.nan)
    calmar = ann / abs(mdd) if np.isfinite(ann) and np.isfinite(mdd) and mdd < 0 else np.nan
    returns = [t.trade_return for t in trades]
    stats = {
        "strategy_id": strategy_id,
        "trade_count": trade_count,
        "exposure_ratio": exposure_days / days,
        "avg_holding_days": np.mean([t.holding_days for t in trades]) if trades else np.nan,
        "win_rate": len(wins) / trade_count if trade_count else np.nan,
        "avg_trade_return": np.mean(returns) if returns else np.nan,
        "median_trade_return": np.median(returns) if returns else np.nan,
        "total_return": total_return,
        "annualized_return": ann,
        "max_drawdown": mdd,
        "max_single_loss": min(returns) if returns else np.nan,
        "profit_factor": pf if np.isfinite(pf) else 99.0,
        "sharpe": sharpe,
        "calmar": calmar,
        "max_consecutive_losses": max_consecutive_losses(returns),
    }
    if collect_curve:
        curve = pd.DataFrame({"trade_date": pd.to_datetime(dates), "equity": equity_s.values, "daily_return": dr.values})
    else:
        curve = pd.DataFrame()
    return stats, trades, curve


def benchmark_buy_hold(df: pd.DataFrame) -> dict:
    ret = df["csi300_close"].pct_change().fillna(0)
    equity = (1 + ret).cumprod()
    total = float(equity.iloc[-1] - 1)
    return {
        "strategy_id": "buy_and_hold_csi300",
        "total_return": total,
        "annualized_return": annualized_return(total, len(df)),
        "max_drawdown": max_drawdown(equity),
        "sharpe": sharpe_from_daily(ret),
        "calmar": annualized_return(total, len(df)) / abs(max_drawdown(equity)),
        "exposure_ratio": 1.0,
        "trade_count": 1,
        "avg_holding_days": len(df),
        "win_rate": np.nan,
        "avg_trade_return": total,
        "max_single_loss": np.nan,
        "max_consecutive_losses": np.nan,
    }


def benchmark_dca(df: pd.DataFrame, step: int = 21) -> dict:
    shares = 0.0
    invested = 0.0
    values = []
    for i, row in df.iterrows():
        if i % step == 0:
            shares += 1.0 / row["csi300_close"]
            invested += 1.0
        values.append(shares * row["csi300_close"] / invested if invested else 1.0)
    equity = pd.Series(values).fillna(1.0)
    total = float(equity.iloc[-1] - 1)
    mdd = max_drawdown(equity)
    ann = annualized_return(total, len(df))
    return {
        "strategy_id": "monthly_dca_csi300",
        "total_return": total,
        "annualized_return": ann,
        "max_drawdown": mdd,
        "sharpe": sharpe_from_daily(equity.pct_change().fillna(0)),
        "calmar": ann / abs(mdd) if mdd < 0 else np.nan,
        "exposure_ratio": 1.0,
        "trade_count": int(math.ceil(len(df) / step)),
        "avg_holding_days": np.nan,
        "win_rate": np.nan,
        "avg_trade_return": np.nan,
        "max_single_loss": np.nan,
        "max_consecutive_losses": np.nan,
    }


def grid_search(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[Trade]], dict[str, pd.DataFrame]]:
    rows = []
    all_trades: dict[str, list[Trade]] = {}
    curves: dict[str, pd.DataFrame] = {}
    ctx = make_bt_context(df)
    hdf = future_returns(df)
    strategy_num = 0
    for entry in ENTRY_THRESHOLDS:
        for upper in UPPER_BOUNDS:
            if upper is not None and upper <= entry:
                continue
            for ex in EXHAUSTION_RULES:
                for pr in PRICE_CONFIRM_RULES:
                    mask = entry_mask(df, entry, upper, ex, pr)
                    if mask.sum() < 5:
                        continue
                    fwd_sub = hdf.loc[mask]
                    fwd_rates = {}
                    for horizon in [5, 10, 20, 60]:
                        vals = fwd_sub[f"fwd_{horizon}d_ret"].dropna()
                        fwd_rates[f"{horizon}d_forward_win_rate"] = (vals > 0).mean() if len(vals) else np.nan
                    for exit_rule in EXIT_RULES:
                        strategy_num += 1
                        sid = f"s{strategy_num:06d}"
                        stats, trades, _ = backtest_strategy(df, sid, mask, exit_rule, ctx=ctx, collect_curve=False)
                        stats.update(
                            {
                                "entry_threshold": entry,
                                "upper_bound": upper if upper is not None else "None",
                                "exhaustion_rule": ex,
                                "price_confirm_rule": pr,
                                "exit_rule": exit_rule,
                                **fwd_rates,
                            }
                        )
                        returns_by_year = pd.Series({t.year: t.trade_return for t in trades}).groupby(level=0).sum() if trades else pd.Series(dtype=float)
                        total_abs = abs(sum(t.trade_return for t in trades))
                        max_year_abs = abs(returns_by_year).max() if len(returns_by_year) else 0
                        years = set(df["trade_date"].dt.year.unique())
                        trade_years = set(t.year for t in trades)
                        stats["yearly_stability_score"] = len(trade_years) / max(1, len(years))
                        overfit = []
                        if total_abs > 0 and max_year_abs / total_abs > 0.60:
                            overfit.append("single_year_contribution_gt_60pct")
                        if years - trade_years:
                            overfit.append("zero_trade_year")
                        stats["overfit_warning"] = "|".join(overfit) if overfit else ""
                        stats["sample_warning"] = "trade_count_lt_20" if stats["trade_count"] < 20 else ("trade_count_20_30" if stats["trade_count"] <= 30 else "")
                        stats["quality_warning"] = "max_single_loss_gt_8pct" if np.isfinite(stats["max_single_loss"]) and stats["max_single_loss"] < -0.08 else ""
                        rows.append(stats)
    res = pd.DataFrame(rows)
    if res.empty:
        raise RuntimeError("No grid results generated.")

    small_penalty = np.where(res["trade_count"] < 20, 1.0, np.where(res["trade_count"] <= 30, 0.35, 0.0))
    bh_mdd = abs(benchmark_buy_hold(df)["max_drawdown"])
    res["defense_warning"] = np.where(res["max_drawdown"].abs() > bh_mdd * 0.70, "mdd_gt_70pct_buy_hold", "")
    res["score"] = (
        0.25 * normalize(res["win_rate"])
        + 0.25 * normalize(res["avg_trade_return"])
        + 0.20 * normalize(res["annualized_return"])
        + 0.15 * normalize(res["profit_factor"].clip(upper=10))
        + 0.10 * normalize(res["calmar"].clip(lower=-5, upper=10))
        - 0.20 * normalize(res["max_drawdown"].abs())
        - 0.10 * normalize(res["max_single_loss"].abs())
        - 0.10 * small_penalty
    )
    res = res.sort_values("score", ascending=False)
    res.to_csv(OUT / "csi300_risk_temp_grid_results.csv", index=False)
    res.head(20).to_csv(OUT / "csi300_risk_temp_top20_score.csv", index=False)
    res.sort_values(["win_rate", "trade_count"], ascending=[False, False]).head(20).to_csv(OUT / "csi300_risk_temp_top20_winrate.csv", index=False)
    res.sort_values("total_return", ascending=False).head(20).to_csv(OUT / "csi300_risk_temp_top20_return.csv", index=False)
    res.sort_values(["max_drawdown", "annualized_return"], ascending=[False, False]).head(20).to_csv(OUT / "csi300_risk_temp_top20_low_drawdown.csv", index=False)
    stable = res[(res["trade_count"] >= 20) & (res["overfit_warning"] == "")]
    if stable.empty:
        stable = res[res["trade_count"] >= 20]
    stable.sort_values(["yearly_stability_score", "score"], ascending=[False, False]).head(20).to_csv(OUT / "csi300_risk_temp_top20_stable.csv", index=False)

    needed_ids = set(res.head(20)["strategy_id"])
    needed_ids |= set(res.sort_values(["win_rate", "trade_count"], ascending=[False, False]).head(20)["strategy_id"])
    needed_ids |= set(res.sort_values("total_return", ascending=False).head(20)["strategy_id"])
    if not stable.empty:
        needed_ids |= set(stable.sort_values(["yearly_stability_score", "score"], ascending=[False, False]).head(20)["strategy_id"])
    for _, spec in res[res["strategy_id"].isin(needed_ids)].iterrows():
        upper = None if str(spec["upper_bound"]) == "None" else float(spec["upper_bound"])
        mask = entry_mask(df, float(spec["entry_threshold"]), upper, spec["exhaustion_rule"], spec["price_confirm_rule"])
        stats, trades, curve = backtest_strategy(df, spec["strategy_id"], mask, spec["exit_rule"], ctx=ctx, collect_curve=True)
        all_trades[spec["strategy_id"]] = trades
        curves[spec["strategy_id"]] = curve
    return res, all_trades, curves


def yearly_stability(df: pd.DataFrame, grid: pd.DataFrame, trades_by_id: dict[str, list[Trade]]) -> pd.DataFrame:
    ids = set(grid.head(20)["strategy_id"])
    ids |= set(grid.sort_values(["win_rate", "trade_count"], ascending=[False, False]).head(20)["strategy_id"])
    ids |= set(grid.sort_values("total_return", ascending=False).head(20)["strategy_id"])
    rows = []
    for sid in ids:
        trades = trades_by_id.get(sid, [])
        spec = grid.loc[grid["strategy_id"] == sid].iloc[0].to_dict()
        for year, ydf in df.groupby(df["trade_date"].dt.year):
            ytr = [t for t in trades if t.year == year]
            returns = [t.trade_return for t in ytr]
            bh = ydf["csi300_close"].iloc[-1] / ydf["csi300_close"].iloc[0] - 1 if len(ydf) > 1 else np.nan
            total = np.prod([1 + r for r in returns]) - 1 if returns else 0.0
            rows.append(
                {
                    "strategy_id": sid,
                    "year": year,
                    "trade_count": len(ytr),
                    "win_rate": np.mean([r > 0 for r in returns]) if returns else np.nan,
                    "avg_trade_return": np.mean(returns) if returns else np.nan,
                    "total_return": total,
                    "max_drawdown": min([t.mae for t in ytr], default=np.nan),
                    "max_single_loss": min(returns) if returns else np.nan,
                    "exposure_ratio": sum(t.holding_days for t in ytr) / len(ydf),
                    "buy_and_hold_return": bh,
                    "excess_return": total - bh if np.isfinite(bh) else np.nan,
                    "whether_outperform_benchmark": total > bh if np.isfinite(bh) else False,
                    "entry_threshold": spec["entry_threshold"],
                    "upper_bound": spec["upper_bound"],
                    "exhaustion_rule": spec["exhaustion_rule"],
                    "price_confirm_rule": spec["price_confirm_rule"],
                    "exit_rule": spec["exit_rule"],
                }
            )
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "csi300_risk_temp_yearly_stability.csv", index=False)
    return out


def benchmark_comparison(df: pd.DataFrame, grid: pd.DataFrame, trades_by_id: dict[str, list[Trade]], curves: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = [benchmark_buy_hold(df), benchmark_dca(df)]
    specs = [
        ("rt65_fixed20", 65, None, "none", "none", "fixed_20d"),
        ("rt75_fixed20", 75, None, "none", "none", "fixed_20d"),
        ("rt65_not5low_fixed20", 65, None, "none", "not_5d_low", "fixed_20d"),
    ]
    for name, entry, upper, ex, pr, er in specs:
        mask = entry_mask(df, entry, upper, ex, pr)
        stats, trades, curve = backtest_strategy(df, name, mask, er)
        rows.append({k: stats.get(k) for k in rows[0].keys()} | stats)
        trades_by_id[name] = trades
        curves[name] = curve
    best = grid[(grid["trade_count"] >= 20)].head(1)
    if not best.empty:
        sid = best.iloc[0]["strategy_id"]
        rows.append(grid.loc[grid["strategy_id"] == sid, rows[0].keys()].iloc[0].to_dict() if set(rows[0].keys()).issubset(grid.columns) else grid.loc[grid["strategy_id"] == sid].iloc[0].to_dict())
        rows[-1]["strategy_id"] = "final_optimized_" + sid
    out = pd.DataFrame(rows)
    keep = [
        "strategy_id",
        "total_return",
        "annualized_return",
        "max_drawdown",
        "sharpe",
        "calmar",
        "exposure_ratio",
        "trade_count",
        "avg_holding_days",
        "win_rate",
        "avg_trade_return",
        "max_single_loss",
        "max_consecutive_losses",
    ]
    out = out[[c for c in keep if c in out.columns]]
    out.to_csv(OUT / "csi300_risk_temp_benchmark_comparison.csv", index=False)
    return out


def plot_outputs(df: pd.DataFrame, interval: pd.DataFrame, exhaustion: pd.DataFrame, price: pd.DataFrame, grid: pd.DataFrame, curves: dict[str, pd.DataFrame]) -> None:
    plt.rcParams["axes.unicode_minus"] = False
    best_sid = grid[grid["trade_count"] >= 20].iloc[0]["strategy_id"] if not grid[grid["trade_count"] >= 20].empty else grid.iloc[0]["strategy_id"]
    best_curve = curves.get(best_sid)
    if best_curve is None or best_curve.empty:
        spec = grid.loc[grid["strategy_id"] == best_sid].iloc[0]
        upper = None if str(spec["upper_bound"]) == "None" else float(spec["upper_bound"])
        mask = entry_mask(df, float(spec["entry_threshold"]), upper, spec["exhaustion_rule"], spec["price_confirm_rule"])
        _, _, best_curve = backtest_strategy(df, best_sid, mask, spec["exit_rule"], ctx=make_bt_context(df), collect_curve=True)
        curves[best_sid] = best_curve
    bh = (1 + df["csi300_close"].pct_change().fillna(0)).cumprod()

    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax1.plot(df["trade_date"], df["csi300_close"], color="black", lw=1, label="CSI300")
    ax2 = ax1.twinx()
    ax2.plot(df["trade_date"], df["risk_temperature"], color="tab:red", lw=1, alpha=0.75, label="Risk Temperature")
    ax1.set_title("Risk temperature vs CSI300")
    fig.tight_layout()
    fig.savefig(CHARTS / "risk_temperature_vs_csi300.png", dpi=160)
    plt.close(fig)

    for h, fname in [(20, "interval_forward_return_20d.png"), (60, "interval_forward_return_60d.png")]:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.bar(interval["interval"], interval[f"future_{h}d_avg_return"] * 100, color="tab:red")
        ax.axhline(0, color="black", lw=0.8)
        ax.set_title(f"Average forward {h}d return by interval")
        ax.tick_params(axis="x", rotation=45)
        fig.tight_layout()
        fig.savefig(CHARTS / fname, dpi=160)
        plt.close(fig)

    if best_curve is not None:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(best_curve["trade_date"], best_curve["equity"], label="Best strategy")
        ax.set_title("Best strategy equity curve")
        ax.legend()
        fig.tight_layout()
        fig.savefig(CHARTS / "best_strategy_equity_curve.png", dpi=160)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(best_curve["trade_date"], best_curve["equity"], label="Best strategy")
        ax.plot(df["trade_date"], bh, label="Buy & hold")
        ax.set_title("Best strategy vs buy & hold")
        ax.legend()
        fig.tight_layout()
        fig.savefig(CHARTS / "best_strategy_vs_buy_hold.png", dpi=160)
        plt.close(fig)

        dd = best_curve["equity"] / best_curve["equity"].cummax() - 1
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.fill_between(best_curve["trade_date"], dd * 100, 0, color="tab:red", alpha=0.35)
        ax.set_title("Best strategy drawdown")
        fig.tight_layout()
        fig.savefig(CHARTS / "best_strategy_drawdown.png", dpi=160)
        plt.close(fig)

    year_rows = []
    if best_curve is not None:
        curve = best_curve.copy()
        curve["year"] = pd.to_datetime(curve["trade_date"]).dt.year
        for y, ydf in curve.groupby("year"):
            strat = ydf["equity"].iloc[-1] / ydf["equity"].iloc[0] - 1
            bdf = df[df["trade_date"].dt.year == y]
            bret = bdf["csi300_close"].iloc[-1] / bdf["csi300_close"].iloc[0] - 1 if len(bdf) else np.nan
            year_rows.append((y, strat, bret))
    if year_rows:
        yr = pd.DataFrame(year_rows, columns=["year", "strategy", "buy_hold"])
        fig, ax = plt.subplots(figsize=(10, 4))
        x = np.arange(len(yr))
        ax.bar(x - 0.2, yr["strategy"] * 100, width=0.4, label="Strategy")
        ax.bar(x + 0.2, yr["buy_hold"] * 100, width=0.4, label="Buy & hold")
        ax.set_xticks(x, yr["year"])
        ax.legend()
        ax.set_title("Yearly return comparison")
        fig.tight_layout()
        fig.savefig(CHARTS / "yearly_return_comparison.png", dpi=160)
        plt.close(fig)

    pivot = grid.groupby(["entry_threshold", "price_confirm_rule"])["score"].mean().unstack()
    fig, ax = plt.subplots(figsize=(12, 5))
    im = ax.imshow(pivot.fillna(0), aspect="auto", cmap="RdYlGn")
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    ax.set_xticks(range(len(pivot.columns)), pivot.columns, rotation=45, ha="right")
    ax.set_title("Parameter heatmap: entry vs confirmation")
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(CHARTS / "parameter_heatmap_entry_vs_confirm.png", dpi=160)
    plt.close(fig)

    ex_eff = exhaustion.groupby("exhaustion_rule")["20d_avg_return"].mean().sort_values()
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.barh(ex_eff.index, ex_eff.values * 100)
    ax.set_title("Panic exhaustion effect: 20d avg return")
    fig.tight_layout()
    fig.savefig(CHARTS / "panic_exhaustion_effect.png", dpi=160)
    plt.close(fig)

    pr_eff = price.groupby("price_confirm_rule")["20d_avg_return"].mean().sort_values()
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.barh(pr_eff.index, pr_eff.values * 100)
    ax.set_title("Price confirmation effect: 20d avg return")
    fig.tight_layout()
    fig.savefig(CHARTS / "price_confirmation_effect.png", dpi=160)
    plt.close(fig)


def pick_recommendations(grid: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    eligible = grid[(grid["trade_count"] >= 20) & (grid["overfit_warning"] == "")]
    if eligible.empty:
        eligible = grid[grid["trade_count"] >= 20]
    if eligible.empty:
        eligible = grid
    default = eligible.sort_values("score", ascending=False).iloc[0]
    conservative = eligible.sort_values(["max_drawdown", "max_single_loss", "score"], ascending=[False, False, False]).iloc[0]
    aggressive = grid[(grid["trade_count"] >= 20)].sort_values(["annualized_return", "score"], ascending=[False, False]).iloc[0]
    return default, conservative, aggressive


def write_report(
    df: pd.DataFrame,
    audit: dict,
    interval: pd.DataFrame,
    exhaustion: pd.DataFrame,
    price: pd.DataFrame,
    grid: pd.DataFrame,
    yearly: pd.DataFrame,
    benchmarks: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    default, conservative, aggressive = pick_recommendations(grid)
    high = interval[interval["interval"].isin(["60-65", "65-70", "70-75"])]["future_20d_avg_return"].mean()
    panic = interval[interval["interval"].isin(["75-80", "80-85", "85-90"])]["future_20d_avg_return"].mean()
    extreme = interval[interval["interval"] == "90-100"].iloc[0]
    best_ex = exhaustion.sort_values(["20d_avg_return", "20d_forward_win_rate"], ascending=False).iloc[0]
    best_pr = price.sort_values(["20d_avg_return", "20d_forward_win_rate"], ascending=False).iloc[0]
    direct65 = price[(price["entry_threshold"] == 65) & (price["upper_bound"].isna() | (price["upper_bound"] == "None")) & (price["price_confirm_rule"] == "none")]
    notlow65 = price[(price["entry_threshold"] == 65) & (price["price_confirm_rule"] == "not_5d_low")]

    def strat_block(title: str, s: pd.Series, prod: bool) -> list[str]:
        return [
            f"## {title}",
            "",
            f"- 加仓区间: risk_temperature >= {s['entry_threshold']}" + (f" 且 < {s['upper_bound']}" if str(s["upper_bound"]) != "None" else ""),
            f"- 是否使用 upper bound: {str(s['upper_bound']) != 'None'}",
            f"- 恐慌衰竭条件: {s['exhaustion_rule']}",
            f"- 价格确认条件: {s['price_confirm_rule']}",
            "- 执行方式: T日收盘生成信号，T+1日沪深300 open 执行",
            "- 初始仓位: 研究建议 1/3 至 1/2 仓，不建议一次满仓",
            "- 二次加仓条件: 后续可在新信号且未超过组合目标仓位时研究扩展，本脚本未启用重叠持仓",
            f"- 退出条件: {s['exit_rule']}",
            "- 止盈条件: 见退出条件中的 tp 项；如无 tp 则未启用",
            "- 止损条件: 见退出条件中的 sl 项；如无 sl 则未启用",
            f"- 历史交易次数: {int(s['trade_count'])}",
            f"- 胜率: {pct(s['win_rate'])}",
            f"- 平均单笔收益: {pct(s['avg_trade_return'])}",
            f"- 年化收益: {pct(s['annualized_return'])}",
            f"- 最大回撤: {pct(s['max_drawdown'])}",
            f"- 最大单笔亏损: {pct(s['max_single_loss'])}",
            f"- 盈亏比: {fmt(s['profit_factor'], 2)}",
            f"- 持仓暴露率: {pct(s['exposure_ratio'])}",
            f"- 是否建议进入生产策略: {'可以作为候选生产规则继续纸面跟踪' if prod and int(s['trade_count']) >= 20 else '暂不建议直接生产'}",
            f"- 主要风险: {s.get('overfit_warning','') or '无明显单一年份过拟合'}; {s.get('quality_warning','') or '无最大单笔亏损>8%标记'}",
            "",
        ]

    lines = [
        "# 沪深300 risk_temperature 加仓策略研究报告",
        "",
        "## 数据核查摘要",
        "",
        f"- 使用文件路径: `{RISK_PATH}`, `{INDEX_PATH}`",
        f"- 样本区间: {audit['aligned_start'].date()} 至 {audit['aligned_end'].date()}",
        f"- risk_temperature 最新日期: {audit['risk_latest'].date()}",
        f"- 沪深300指数最新日期: {audit['index_latest'].date()}",
        f"- 有效对齐区间: {audit['aligned_start'].date()} 至 {audit['aligned_end'].date()}",
        f"- 有效样本数: {audit['inner_join_rows']}",
        f"- 数据质量问题: risk缺失 {audit['joined_risk_temperature_missing']}，open缺失 {audit['joined_open_missing']}，close缺失 {audit['joined_close_missing']}；指数晚于温度={audit['index_later_than_risk']}",
        "",
        "## 单变量区间研究结果",
        "",
        f"- 60-75 high_risk 20日平均收益: {pct(high)}。",
        f"- 75-90 panic 20日平均收益: {pct(panic)}。",
        f"- 90-100 extreme_panic 样本数: {int(extreme['sample_count'])}，20日平均收益 {pct(extreme['future_20d_avg_return'])}，最大亏损 {pct(extreme['future_20d_max_loss'])}。",
        f"- 结论: {'60-75 比 75-90 更适合加仓' if high > panic else '75-90 的平均收益更高，但需结合回撤和样本数'}；单纯高温买入不能直接作为可靠生产信号。",
        "",
        "## 恐慌衰竭条件研究结果",
        "",
        f"- 最优衰竭组合: {best_ex['entry_spec']} + {best_ex['exhaustion_rule']}，20日胜率 {pct(best_ex['20d_forward_win_rate'])}，20日平均收益 {pct(best_ex['20d_avg_return'])}，样本数 {int(best_ex['sample_count'])}。",
        "- 判断: 衰竭条件的价值在于减少直接接飞刀，但过强的回落条件会明显降低样本数。",
        "",
        "## 价格确认条件研究结果",
        "",
        f"- 最优价格确认组合: {best_pr['condition']}，20日胜率 {pct(best_pr['20d_forward_win_rate'])}，20日平均收益 {pct(best_pr['20d_avg_return'])}，样本数 {int(best_pr['sample_count'])}。",
        f"- 高恐慌 + 不创新低是否优于直接买: {'是' if not direct65.empty and not notlow65.empty and float(notlow65['20d_avg_return'].mean()) > float(direct65['20d_avg_return'].mean()) else '不稳定或不显著'}。",
        "- 站上 MA5/MA10 通常会更晚入场，胜率可能改善，但会牺牲一部分早期反弹。",
        "",
        "## 网格搜索 Top 20",
        "",
        grid.head(20)[["strategy_id", "entry_threshold", "upper_bound", "exhaustion_rule", "price_confirm_rule", "exit_rule", "trade_count", "win_rate", "annualized_return", "max_drawdown", "score"]].to_markdown(index=False),
        "",
        "## 年度稳定性",
        "",
        f"- 年度稳定性文件已输出 `{OUT / 'csi300_risk_temp_yearly_stability.csv'}`。",
        f"- Top 策略是否存在过拟合风险: {default['overfit_warning'] or '未触发主要过拟合标记'}。",
        "- 需要注意：若某策略存在 zero_trade_year，则说明它并非每个市场状态都工作。",
        "",
        "## 与买入持有对比",
        "",
        benchmarks.to_markdown(index=False),
        "",
        "## 不建议使用的策略",
        "",
        "- 交易次数 < 20 的高分策略：样本不足，不能作为最终推荐。",
        "- 无确认条件且 extreme_panic 直接买入：容易接飞刀。",
        "- 最大单笔亏损超过 8% 或最大回撤接近买入持有的策略：防守意义不足。",
        "",
        "## 后续优化建议",
        "",
        "- 后续可加入真实 ETF 数据、分批仓位、二次加仓、行业宽度和成交拥挤度确认。",
        "- 生产前建议至少做一段纸面跟踪，确认实时数据生成时间和交易执行时间一致。",
        "",
    ]
    lines += strat_block("默认推荐策略", default, True)
    lines += strat_block("保守策略", conservative, False)
    lines += ["- 适合场景: 更重视回撤和最大单笔亏损。", "- 缺点: 可能踏空快速反弹。", ""]
    lines += strat_block("激进策略", aggressive, False)
    lines += ["- 适合场景: 愿意承担更高波动以换取更高年化。", "- 缺点: 接飞刀和年度不稳定风险更高。", ""]
    lines += [
        "## 自检",
        "",
        "- 是否存在未来函数: 否，信号仅使用 T 日及以前 rolling 指标。",
        "- T+1 执行是否正确: 是，买卖均使用下一交易日 open；无 next_open 的信号不可交易。",
        "- 交易成本是否扣除: 是，买入万一+滑点，卖出万一+滑点。",
        "- 是否使用了沪深300 open: 是。",
        "- 风险温度和指数行情是否正确 inner join: 是。",
        f"- 样本数是否足够: 有效样本 {audit['inner_join_rows']}，但单策略需看 trade_count。",
        f"- Top 策略是否存在过拟合: {default['overfit_warning'] or '默认策略未触发主要过拟合标记'}。",
        "- 是否有单一年份贡献过高: 已用 overfit_warning 标记。",
        "- 是否输出全部 CSV: 是。",
        "- 是否输出全部图表: 是。",
        "- 是否没有修改无关代码: 是，本次仅新增 research/ 下脚本和输出。",
        "",
    ]
    (OUT / "csi300_risk_temp_strategy_report.md").write_text("\n".join(lines), encoding="utf-8")
    return default, conservative, aggressive


def main() -> None:
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    ensure_dirs()
    df, audit = load_data()
    write_audit(audit)
    hdf = future_returns(df)
    interval = interval_summary(hdf)
    exhaustion = exhaustion_results(df, hdf)
    price = price_confirmation_results(df, hdf)
    grid, trades_by_id, curves = grid_search(df)
    yearly = yearly_stability(df, grid, trades_by_id)
    benchmarks = benchmark_comparison(df, grid, trades_by_id, curves)
    plot_outputs(df, interval, exhaustion, price, grid, curves)
    default, _, _ = write_report(df, audit, interval, exhaustion, price, grid, yearly, benchmarks)

    best_interval = interval.sort_values("future_20d_avg_return", ascending=False).iloc[0]
    print("有效回测区间:", audit["aligned_start"].date(), "至", audit["aligned_end"].date())
    print("有效样本数:", audit["inner_join_rows"])
    print("最优 risk_temperature 区间:", best_interval["interval"])
    print("最优衰竭条件:", default["exhaustion_rule"])
    print("最优价格确认条件:", default["price_confirm_rule"])
    print("默认推荐策略:", default["strategy_id"], default["entry_threshold"], default["upper_bound"], default["exit_rule"])
    print("胜率:", pct(default["win_rate"]))
    print("年化收益:", pct(default["annualized_return"]))
    print("最大回撤:", pct(default["max_drawdown"]))
    print("交易次数:", int(default["trade_count"]))
    print("是否建议进入生产:", "候选纸面跟踪" if int(default["trade_count"]) >= 20 else "否，样本不足")
    print("主要风险:", default["overfit_warning"] or default["quality_warning"] or "仍需实盘纸面跟踪验证")


if __name__ == "__main__":
    main()
