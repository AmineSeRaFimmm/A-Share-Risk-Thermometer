from __future__ import annotations

import math
from typing import Iterable

import pandas as pd


WINDOWS = {
    "1Y": 252,
    "2Y": 504,
}
HORIZONS = [1, 5, 10]
TEMP_BINS = [
    ("低温", 0, 40),
    ("正常", 40, 60),
    ("警戒", 60, 75),
    ("高风险", 75, 90),
    ("恐慌", 90, 101),
]


def _finite(value, digits: int = 4):
    if value is None:
        return None
    try:
        numeric = float(value)
    except Exception:
        return value
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


def _corr(left: pd.Series, right: pd.Series) -> float | None:
    frame = pd.concat([left, right], axis=1).dropna()
    if len(frame) < 30:
        return None
    if frame.iloc[:, 0].nunique() < 3 or frame.iloc[:, 1].nunique() < 3:
        return None
    return float(frame.iloc[:, 0].corr(frame.iloc[:, 1]))


def _label_strength(corr: float | None) -> str:
    if corr is None:
        return "样本不足"
    abs_corr = abs(corr)
    if abs_corr >= 0.45:
        return "强"
    if abs_corr >= 0.30:
        return "中"
    if abs_corr >= 0.18:
        return "弱"
    return "很弱"


def _stability(row: pd.Series, corr_2y: float | None) -> str:
    corr_1y = row.get("corr_temp_fwd_excess")
    if corr_1y is None or corr_2y is None or pd.isna(corr_1y) or pd.isna(corr_2y):
        return "样本不足"
    if corr_1y == 0 or corr_2y == 0 or math.copysign(1, corr_1y) != math.copysign(1, corr_2y):
        return "方向不稳"
    if abs(corr_1y) >= 0.30 and abs(corr_2y) >= 0.20:
        return "稳定"
    if abs(corr_1y) >= 0.25 and abs(corr_2y) >= 0.15:
        return "观察"
    return "偏弱"


def _safe_mean(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return None if clean.empty else float(clean.mean())


def _win_rate(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return None if clean.empty else float((clean > 0).mean())


def _max_drawdown_from_returns(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    curve = (1 + clean).cumprod()
    dd = curve / curve.cummax() - 1
    return float(dd.min())


def _future_returns(close: pd.Series, horizons: Iterable[int]) -> pd.DataFrame:
    out = pd.DataFrame(index=close.index)
    out["ret_1d"] = close.pct_change()
    for horizon in horizons:
        out[f"fwd_{horizon}d"] = close.shift(-horizon) / close - 1
    return out


def _sector_panel(sectors: pd.DataFrame, benchmark: pd.DataFrame, risk: pd.DataFrame) -> pd.DataFrame:
    sector = sectors.copy()
    sector["date"] = pd.to_datetime(sector["date"]).dt.strftime("%Y-%m-%d")
    sector["close"] = pd.to_numeric(sector["close"], errors="coerce")
    sector = sector.dropna(subset=["date", "symbol", "name", "close"]).sort_values(["symbol", "date"])

    bench = benchmark[benchmark["symbol"].astype(str) == "sh000300"].copy()
    bench["date"] = pd.to_datetime(bench["date"]).dt.strftime("%Y-%m-%d")
    bench["close"] = pd.to_numeric(bench["close"], errors="coerce")
    bench = bench.dropna(subset=["date", "close"]).sort_values("date").set_index("date")
    bench_ret = _future_returns(bench["close"], HORIZONS)
    bench_ret = bench_ret.rename(columns={c: f"benchmark_{c}" for c in bench_ret.columns}).reset_index()

    risk_df = risk[["trade_date", "risk_temperature"]].copy()
    risk_df["date"] = pd.to_datetime(risk_df["trade_date"]).dt.strftime("%Y-%m-%d")
    risk_df["risk_temperature"] = pd.to_numeric(risk_df["risk_temperature"], errors="coerce")
    risk_df = risk_df.dropna(subset=["date", "risk_temperature"]).sort_values("date")
    risk_df["risk_temperature_delta_1d"] = risk_df["risk_temperature"].diff()

    frames = []
    for (symbol, name), group in sector.groupby(["symbol", "name"], sort=True):
        work = group.sort_values("date").set_index("date")
        rets = _future_returns(work["close"], HORIZONS).reset_index()
        rets["symbol"] = symbol
        rets["name"] = name
        frames.append(rets)
    panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if panel.empty:
        return panel

    panel = panel.merge(bench_ret, on="date", how="left").merge(
        risk_df[["date", "risk_temperature", "risk_temperature_delta_1d"]],
        on="date",
        how="left",
    )
    for horizon in HORIZONS:
        panel[f"fwd_excess_{horizon}d"] = panel[f"fwd_{horizon}d"] - panel[f"benchmark_fwd_{horizon}d"]
    panel["excess_ret_1d"] = panel["ret_1d"] - panel["benchmark_ret_1d"]
    return panel.sort_values(["symbol", "date"])


def _bin_stats(window: pd.DataFrame, return_col: str) -> list[dict]:
    out = []
    for label, low, high in TEMP_BINS:
        bucket = window[(window["risk_temperature"] >= low) & (window["risk_temperature"] < high)]
        out.append({
            "bucket": label,
            "range": f"{low}-{high - 1 if high > 100 else high}",
            "sample_size": int(bucket[return_col].dropna().shape[0]),
            "avg_return": _finite(_safe_mean(bucket[return_col])),
            "win_rate": _finite(_win_rate(bucket[return_col])),
        })
    return out


def analyze_sector_correlation(
    risk: pd.DataFrame,
    sectors: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> dict:
    panel = _sector_panel(sectors, benchmark, risk)
    if panel.empty:
        raise ValueError("sector panel is empty")

    latest_date = max(d for d in panel["date"].dropna().unique().tolist() if d <= str(risk["trade_date"].max()))
    panel = panel[panel["date"] <= latest_date].copy()
    benchmark_latest = benchmark[benchmark["symbol"].astype(str) == "sh000300"]["date"].max()

    rows: list[dict] = []
    bin_rows: list[dict] = []
    for window_name, window_days in WINDOWS.items():
        available_dates = sorted(panel["date"].dropna().unique().tolist())
        keep_dates = set(available_dates[-window_days:])
        win_panel = panel[panel["date"].isin(keep_dates)].copy()

        for (symbol, name), group in win_panel.groupby(["symbol", "name"], sort=True):
            group = group.sort_values("date")
            for horizon in HORIZONS:
                fwd_col = f"fwd_{horizon}d"
                excess_col = f"fwd_excess_{horizon}d"
                valid = group.dropna(subset=["risk_temperature", fwd_col, excess_col])
                rows.append({
                    "window": window_name,
                    "horizon": f"{horizon}D",
                    "symbol": str(symbol),
                    "name": str(name),
                    "sample_size": int(len(valid)),
                    "start_date": None if valid.empty else str(valid["date"].iloc[0]),
                    "end_date": None if valid.empty else str(valid["date"].iloc[-1]),
                    "corr_temp_fwd_return": _finite(_corr(group["risk_temperature"], group[fwd_col])),
                    "corr_delta_temp_fwd_return": _finite(_corr(group["risk_temperature_delta_1d"], group[fwd_col])),
                    "corr_temp_fwd_excess": _finite(_corr(group["risk_temperature"], group[excess_col])),
                    "corr_delta_temp_fwd_excess": _finite(_corr(group["risk_temperature_delta_1d"], group[excess_col])),
                    "corr_temp_sync_return": _finite(_corr(group["risk_temperature"], group["ret_1d"])),
                    "corr_temp_sync_excess": _finite(_corr(group["risk_temperature"], group["excess_ret_1d"])),
                    "avg_fwd_return": _finite(_safe_mean(group[fwd_col])),
                    "avg_fwd_excess": _finite(_safe_mean(group[excess_col])),
                    "high_risk_sample": int(group[group["risk_temperature"] >= 75][excess_col].dropna().shape[0]),
                    "low_risk_sample": int(group[group["risk_temperature"] < 40][excess_col].dropna().shape[0]),
                    "high_risk_avg_excess": _finite(_safe_mean(group[group["risk_temperature"] >= 75][excess_col])),
                    "low_risk_avg_excess": _finite(_safe_mean(group[group["risk_temperature"] < 40][excess_col])),
                    "high_risk_win_rate": _finite(_win_rate(group[group["risk_temperature"] >= 75][excess_col])),
                    "high_risk_max_drawdown": _finite(_max_drawdown_from_returns(group[group["risk_temperature"] >= 75]["ret_1d"])),
                    "strength": _label_strength(_corr(group["risk_temperature"], group[excess_col])),
                })

            bin_payload = _bin_stats(group, "fwd_excess_5d")
            bin_rows.append({
                "window": window_name,
                "symbol": str(symbol),
                "name": str(name),
                "metric": "fwd_excess_5d",
                "buckets": bin_payload,
            })

    metrics = pd.DataFrame(rows)
    if metrics.empty:
        raise ValueError("sector metrics are empty")

    two_year = metrics[(metrics["window"] == "2Y") & (metrics["horizon"] == "5D")][
        ["symbol", "corr_temp_fwd_excess"]
    ].rename(columns={"corr_temp_fwd_excess": "corr_2y"})
    ranking_base = metrics[(metrics["window"] == "1Y") & (metrics["horizon"] == "5D")].merge(
        two_year,
        on="symbol",
        how="left",
    )
    ranking_base["stability"] = ranking_base.apply(lambda row: _stability(row, row.get("corr_2y")), axis=1)
    ranking_base["abs_score"] = ranking_base["corr_temp_fwd_excess"].abs()
    ranking_base = ranking_base.sort_values(["abs_score", "sample_size"], ascending=[False, False])

    def _rank_rows(frame: pd.DataFrame) -> list[dict]:
        out = []
        for row in frame.head(10).itertuples(index=False):
            out.append({
                "symbol": row.symbol,
                "name": row.name,
                "window": row.window,
                "horizon": row.horizon,
                "sample_size": int(row.sample_size),
                "corr_temp_fwd_excess": _finite(row.corr_temp_fwd_excess),
                "corr_2y": _finite(row.corr_2y),
                "corr_delta_temp_fwd_excess": _finite(row.corr_delta_temp_fwd_excess),
                "high_risk_sample": int(row.high_risk_sample),
                "low_risk_sample": int(row.low_risk_sample),
                "high_risk_avg_excess": _finite(row.high_risk_avg_excess),
                "low_risk_avg_excess": _finite(row.low_risk_avg_excess),
                "high_risk_win_rate": _finite(row.high_risk_win_rate),
                "stability": row.stability,
                "strength": _label_strength(row.corr_temp_fwd_excess),
            })
        return out

    positive = ranking_base[ranking_base["corr_temp_fwd_excess"] > 0].sort_values(
        ["corr_temp_fwd_excess", "sample_size"], ascending=[False, False]
    )
    negative = ranking_base[ranking_base["corr_temp_fwd_excess"] < 0].sort_values(
        ["corr_temp_fwd_excess", "sample_size"], ascending=[True, False]
    )

    heatmap = metrics[(metrics["horizon"] == "5D")][[
        "window", "symbol", "name", "corr_temp_fwd_excess", "corr_delta_temp_fwd_excess",
        "corr_temp_sync_excess", "sample_size",
    ]].sort_values(["window", "name"])

    return {
        "methodology": {
            "universe": "申万一级行业指数",
            "benchmark": "沪深300",
            "windows": WINDOWS,
            "horizons": [f"{h}D" for h in HORIZONS],
            "primary_metric": "corr(risk_temperature, future sector excess return over HS300, 5D)",
            "notes": [
                "收益相关性使用行业未来收益和相对沪深300超额收益，不使用价格点位相关。",
                "强正/强反按近一年5日未来超额收益相关排序，并用近两年方向一致性标注稳定性。",
                "相关性不代表因果，只用于识别风险温度环境下的板块倾向。",
            ],
        },
        "as_of": str(latest_date),
        "benchmark_latest_date": None if pd.isna(benchmark_latest) else str(benchmark_latest),
        "sector_count": int(panel["symbol"].nunique()),
        "rankings": {
            "positive": _rank_rows(positive),
            "negative": _rank_rows(negative),
        },
        "metrics": metrics.sort_values(["window", "horizon", "name"]).to_dict(orient="records"),
        "heatmap": heatmap.to_dict(orient="records"),
        "bins": bin_rows,
    }
