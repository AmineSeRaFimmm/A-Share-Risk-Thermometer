from __future__ import annotations

import math
from typing import Iterable

import pandas as pd


WINDOWS = {"1Y": 252, "2Y": 504}
POSITION_WINDOWS = {"3Y": 756, "5Y": 1260}
HORIZONS = [5, 10, 20]
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


def _mean(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return None if clean.empty else float(clean.mean())


def _win_rate(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return None if clean.empty else float((clean > 0).mean())


def _future_returns(close: pd.Series, horizons: Iterable[int]) -> pd.DataFrame:
    out = pd.DataFrame(index=close.index)
    out["ret_1d"] = close.pct_change()
    for horizon in horizons:
        out[f"fwd_{horizon}d"] = close.shift(-horizon) / close - 1
    return out


def _percentile_rank(values: pd.Series, current: float) -> float | None:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty or not math.isfinite(current):
        return None
    return float((clean <= current).mean())


def _performance(values: pd.Series) -> float | None:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2 or clean.iloc[0] <= 0:
        return None
    return float(clean.iloc[-1] / clean.iloc[0] - 1)


def _drawdown(values: pd.Series) -> float | None:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty or clean.max() <= 0:
        return None
    return float(clean.iloc[-1] / clean.max() - 1)


def _score_low(value: float | None) -> float:
    return 0.0 if value is None or pd.isna(value) else max(0.0, min(1.0, 1.0 - float(value)))


def _score_drawdown(value: float | None, scale: float = 0.45) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return max(0.0, min(1.0, abs(min(float(value), 0.0)) / scale))


def _score_underperformance(value: float | None, scale: float = 0.45) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return max(0.0, min(1.0, -float(value) / scale))


def _valuation_ranks(valuation: pd.DataFrame) -> pd.DataFrame:
    if valuation is None or valuation.empty:
        return pd.DataFrame(columns=["symbol", "valuation_low_score", "pe_ttm", "pb", "dividend_yield"])
    frame = valuation.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.replace(".SI", "", regex=False)
    for column in ["pe_ttm", "pb", "dividend_yield"]:
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    pe_rank = frame["pe_ttm"].where(frame["pe_ttm"] > 0).rank(pct=True, ascending=True)
    pb_rank = frame["pb"].where(frame["pb"] > 0).rank(pct=True, ascending=True)
    div_rank = frame["dividend_yield"].rank(pct=True, ascending=False)
    frame["valuation_low_score"] = pd.concat([
        1 - pe_rank,
        1 - pb_rank,
        1 - div_rank,
    ], axis=1).mean(axis=1, skipna=True)
    return frame[["symbol", "pe_ttm", "pb", "dividend_yield", "valuation_low_score"]]


def _prepare_panel(sectors: pd.DataFrame, benchmark: pd.DataFrame, risk: pd.DataFrame) -> pd.DataFrame:
    sector = sectors.copy()
    sector["date"] = pd.to_datetime(sector["date"]).dt.strftime("%Y-%m-%d")
    sector["close"] = pd.to_numeric(sector["close"], errors="coerce")
    sector = sector.dropna(subset=["date", "symbol", "name", "close"]).sort_values(["symbol", "date"])

    bench = benchmark[benchmark["symbol"].astype(str) == "sh000300"].copy()
    bench["date"] = pd.to_datetime(bench["date"]).dt.strftime("%Y-%m-%d")
    bench["close"] = pd.to_numeric(bench["close"], errors="coerce")
    bench = bench.dropna(subset=["date", "close"]).sort_values("date").set_index("date")
    bench_rets = _future_returns(bench["close"], HORIZONS).rename(
        columns={column: f"benchmark_{column}" for column in _future_returns(bench["close"], HORIZONS).columns}
    ).reset_index()

    risk_df = risk[["trade_date", "risk_temperature"]].copy()
    risk_df["date"] = pd.to_datetime(risk_df["trade_date"]).dt.strftime("%Y-%m-%d")
    risk_df["risk_temperature"] = pd.to_numeric(risk_df["risk_temperature"], errors="coerce")
    risk_df = risk_df.dropna(subset=["date", "risk_temperature"]).sort_values("date")
    risk_df["risk_temperature_delta_1d"] = risk_df["risk_temperature"].diff()
    risk_df["risk_temperature_delta_5d"] = risk_df["risk_temperature"].diff(5)
    risk_df["risk_temperature_20d_high"] = risk_df["risk_temperature"].rolling(20, min_periods=5).max()
    risk_df["risk_temperature_pullback"] = risk_df["risk_temperature_20d_high"] - risk_df["risk_temperature"]

    frames = []
    for (symbol, name), group in sector.groupby(["symbol", "name"], sort=True):
        work = group.sort_values("date").set_index("date")
        rets = _future_returns(work["close"], HORIZONS).reset_index()
        rets["symbol"] = str(symbol)
        rets["name"] = str(name)
        frames.append(rets)
    panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if panel.empty:
        return panel

    panel = panel.merge(bench_rets, on="date", how="left").merge(
        risk_df[[
            "date", "risk_temperature", "risk_temperature_delta_1d",
            "risk_temperature_delta_5d", "risk_temperature_20d_high",
            "risk_temperature_pullback",
        ]],
        on="date",
        how="left",
    )
    for horizon in HORIZONS:
        panel[f"fwd_excess_{horizon}d"] = panel[f"fwd_{horizon}d"] - panel[f"benchmark_fwd_{horizon}d"]
    panel["excess_ret_1d"] = panel["ret_1d"] - panel["benchmark_ret_1d"]
    return panel.sort_values(["symbol", "date"])


def _position_candidates(
    sectors: pd.DataFrame,
    benchmark: pd.DataFrame,
    valuation: pd.DataFrame,
    as_of: str,
) -> pd.DataFrame:
    sector = sectors.copy()
    sector["date"] = pd.to_datetime(sector["date"]).dt.strftime("%Y-%m-%d")
    sector["close"] = pd.to_numeric(sector["close"], errors="coerce")
    sector = sector[(sector["date"] <= as_of)].dropna(subset=["date", "symbol", "name", "close"])

    bench = benchmark[(benchmark["symbol"].astype(str) == "sh000300")].copy()
    bench["date"] = pd.to_datetime(bench["date"]).dt.strftime("%Y-%m-%d")
    bench["close"] = pd.to_numeric(bench["close"], errors="coerce")
    bench = bench[(bench["date"] <= as_of)].dropna(subset=["date", "close"]).sort_values("date")

    rows = []
    for (symbol, name), group in sector.groupby(["symbol", "name"], sort=True):
        group = group.sort_values("date")
        if group.empty:
            continue
        current_close = float(group["close"].iloc[-1])
        row = {
            "symbol": str(symbol),
            "name": str(name),
            "current_close": current_close,
            "last_date": str(group["date"].iloc[-1]),
            "sample_days": int(len(group)),
        }
        for label, days in POSITION_WINDOWS.items():
            window = group.tail(days)
            bench_window = bench[bench["date"].isin(window["date"])]
            row[f"price_percentile_{label.lower()}"] = _percentile_rank(window["close"], current_close)
            row[f"drawdown_{label.lower()}"] = _drawdown(window["close"])
            sector_perf = _performance(window["close"])
            bench_perf = _performance(bench_window["close"])
            row[f"return_{label.lower()}"] = sector_perf
            row[f"benchmark_return_{label.lower()}"] = bench_perf
            row[f"relative_return_{label.lower()}"] = None if sector_perf is None or bench_perf is None else sector_perf - bench_perf
        rows.append(row)

    candidates = pd.DataFrame(rows)
    value = _valuation_ranks(valuation)
    if not value.empty:
        candidates = candidates.merge(value, on="symbol", how="left")
    else:
        candidates["valuation_low_score"] = None
        candidates["pe_ttm"] = None
        candidates["pb"] = None
        candidates["dividend_yield"] = None

    candidates["low_position_score"] = (
        candidates["price_percentile_5y"].map(_score_low) * 24
        + candidates["price_percentile_3y"].map(_score_low) * 22
        + candidates["drawdown_5y"].map(_score_drawdown) * 18
        + candidates["drawdown_3y"].map(_score_drawdown) * 14
        + candidates["relative_return_5y"].map(lambda x: _score_underperformance(x, 0.55)) * 10
        + candidates["relative_return_3y"].map(lambda x: _score_underperformance(x, 0.45)) * 7
        + candidates["valuation_low_score"].map(lambda x: 0.0 if x is None or pd.isna(x) else max(0.0, min(1.0, float(x)))) * 5
    )
    return candidates.sort_values(["low_position_score", "sample_days"], ascending=[False, False])


def _bucket_stats(group: pd.DataFrame, return_col: str) -> list[dict]:
    rows = []
    for label, low, high in TEMP_BINS:
        bucket = group[(group["risk_temperature"] >= low) & (group["risk_temperature"] < high)]
        rows.append({
            "bucket": label,
            "range": f"{low}-{high - 1 if high > 100 else high}",
            "sample_size": int(bucket[return_col].dropna().shape[0]),
            "avg_excess": _finite(_mean(bucket[return_col])),
            "win_rate": _finite(_win_rate(bucket[return_col])),
        })
    return rows


def _signal_stats(group: pd.DataFrame, horizon: int) -> list[dict]:
    excess_col = f"fwd_excess_{horizon}d"
    signals = {
        "high_temp": group["risk_temperature"] >= 75,
        "high_temp_falling_5d": (group["risk_temperature"] >= 75) & (group["risk_temperature_delta_5d"] < 0),
        "risk_pullback_after_high": (group["risk_temperature_20d_high"] >= 75) & (group["risk_temperature_pullback"] >= 5),
    }
    out = []
    for name, mask in signals.items():
        sample = group[mask]
        out.append({
            "signal": name,
            "horizon": f"{horizon}D",
            "sample_size": int(sample[excess_col].dropna().shape[0]),
            "avg_fwd_excess": _finite(_mean(sample[excess_col])),
            "win_rate": _finite(_win_rate(sample[excess_col])),
        })
    return out


def _classify(metrics: pd.DataFrame, signals: list[dict], symbol: str) -> str:
    one = metrics[
        (metrics["symbol"] == symbol)
        & (metrics["window"] == "1Y")
        & (metrics["horizon"] == "20D")
    ]
    corr = None if one.empty else one.iloc[0].get("corr_temp_fwd_excess")
    pullback = [
        row for row in signals
        if row["symbol"] == symbol and row["window"] == "1Y" and row["horizon"] == "20D"
        and row["signal"] == "risk_pullback_after_high"
    ]
    if corr is not None and not pd.isna(corr):
        if corr >= 0.18:
            return "高温受益观察"
        if corr <= -0.18:
            return "高温受损观察"
    if pullback and pullback[0]["sample_size"] >= 5 and (pullback[0]["avg_fwd_excess"] or 0) > 0:
        return "高温回落修复观察"
    return "关系偏弱或样本不足"


def analyze_low_position_sector_study(
    risk: pd.DataFrame,
    sectors: pd.DataFrame,
    benchmark: pd.DataFrame,
    valuation: pd.DataFrame | None = None,
    selected_count: int = 5,
) -> dict:
    panel = _prepare_panel(sectors, benchmark, risk)
    if panel.empty:
        raise ValueError("sector panel is empty")
    risk_max = pd.to_datetime(risk["trade_date"]).dt.strftime("%Y-%m-%d").max()
    latest_date = max(d for d in panel["date"].dropna().unique().tolist() if d <= risk_max)
    panel = panel[panel["date"] <= latest_date].copy()

    candidates = _position_candidates(sectors, benchmark, valuation if valuation is not None else pd.DataFrame(), latest_date)
    selected = candidates.head(selected_count).copy()
    selected_symbols = set(selected["symbol"].astype(str))
    available_dates = sorted(panel["date"].dropna().unique().tolist())

    metric_rows: list[dict] = []
    bucket_rows: list[dict] = []
    signal_rows: list[dict] = []
    for window_name, days in WINDOWS.items():
        keep_dates = set(available_dates[-days:])
        win_panel = panel[panel["date"].isin(keep_dates) & panel["symbol"].isin(selected_symbols)].copy()
        for (symbol, name), group in win_panel.groupby(["symbol", "name"], sort=True):
            group = group.sort_values("date")
            for horizon in HORIZONS:
                fwd_col = f"fwd_{horizon}d"
                excess_col = f"fwd_excess_{horizon}d"
                valid = group.dropna(subset=["risk_temperature", fwd_col, excess_col])
                metric_rows.append({
                    "window": window_name,
                    "horizon": f"{horizon}D",
                    "symbol": str(symbol),
                    "name": str(name),
                    "sample_size": int(len(valid)),
                    "start_date": None if valid.empty else str(valid["date"].iloc[0]),
                    "end_date": None if valid.empty else str(valid["date"].iloc[-1]),
                    "corr_temp_fwd_return": _finite(_corr(group["risk_temperature"], group[fwd_col])),
                    "corr_temp_fwd_excess": _finite(_corr(group["risk_temperature"], group[excess_col])),
                    "corr_delta_temp_fwd_excess": _finite(_corr(group["risk_temperature_delta_1d"], group[excess_col])),
                    "avg_fwd_return": _finite(_mean(group[fwd_col])),
                    "avg_fwd_excess": _finite(_mean(group[excess_col])),
                    "win_rate_excess": _finite(_win_rate(group[excess_col])),
                })
                for row in _signal_stats(group, horizon):
                    signal_rows.append({"window": window_name, "symbol": str(symbol), "name": str(name), **row})
            bucket_rows.append({
                "window": window_name,
                "symbol": str(symbol),
                "name": str(name),
                "metric": "fwd_excess_20d",
                "buckets": _bucket_stats(group, "fwd_excess_20d"),
            })

    metrics = pd.DataFrame(metric_rows)
    signals = signal_rows
    selected_payload = []
    for row in selected.itertuples(index=False):
        item = row._asdict()
        item["relationship_type"] = _classify(metrics, signals, str(item["symbol"]))
        selected_payload.append({key: _finite(value) for key, value in item.items()})

    return {
        "methodology": {
            "universe": "申万一级行业指数",
            "benchmark": "沪深300",
            "as_of_rule": "使用不晚于风险温度最新正式日期的行业指数收盘数据",
            "low_position_score": {
                "price_percentile_5y": "24%",
                "price_percentile_3y": "22%",
                "drawdown_5y": "18%",
                "drawdown_3y": "14%",
                "relative_underperformance_5y": "10%",
                "relative_underperformance_3y": "7%",
                "current_cross_section_valuation": "5%",
            },
            "valuation_note": "AkShare当前接口提供申万一级PE/PB快照，但不是历史估值序列；估值只作当前横截面辅助，不伪装为3Y/5Y历史估值分位。",
            "relationship_windows": WINDOWS,
            "horizons": [f"{h}D" for h in HORIZONS],
            "primary_relationship_metric": "corr(risk_temperature, future sector excess return over HS300, 20D)",
            "notes": [
                "低位不是买入信号，只表示价格位置、回撤、相对弱势和当前估值横截面组合后的候选。",
                "关系研究使用未来收益和未来超额收益，避免价格点位相关造成伪相关。",
                "高温回落信号用于观察风险温度见顶后低位板块是否有修复倾向。",
            ],
        },
        "as_of": str(latest_date),
        "sector_count": int(panel["symbol"].nunique()),
        "selected_count": int(len(selected_payload)),
        "selected_sectors": selected_payload,
        "all_candidates": [
            {key: _finite(value) for key, value in row.items()}
            for row in candidates.head(12).to_dict(orient="records")
        ],
        "metrics": metrics.sort_values(["window", "horizon", "name"]).to_dict(orient="records"),
        "buckets": bucket_rows,
        "signals": signals,
    }
