from __future__ import annotations
import numpy as np
import pandas as pd
from src.core.breadth import compute_index_breadth_proxy
from src.core.data_quality import observation_quality_score
from src.utils.config import load_regimes, load_thresholds, load_weights
from src.utils.quality import clip, merge_quality

WEIGHTS = load_weights()
REGIMES = load_regimes()
_THRESHOLDS = load_thresholds()
# Align rolling percentile warmup with config/thresholds.yml (was hard-coded 20).
_PERCENTILE_MIN_PERIODS = int(_THRESHOLDS["min_history_days_for_percentile"])


def regime_for(temp: float) -> tuple[str, str]:
    for upper, code, cn in REGIMES:
        if temp < upper:
            return code, cn
    return "EXTREME_PANIC", "极端恐慌"

def interpretation(temp: float, regime_cn: str, row: pd.Series) -> dict:
    posture = {
        "平静": "保持常规观察",
        "正常": "按主策略执行",
        "警戒": "降低追高倾向",
        "高风险": "防守 + 等待风险释放",
        "恐慌区": "防守 + 观察反身修复",
        "极端恐慌": "强确认后再行动",
    }.get(regime_cn, "观察")
    component_names = {
        "avix_percentile_2y": "AVIX两年分位",
        "avix_zscore_1y": "AVIX异常程度",
        "avix_5d_change": "AVIX短期变化",
        "qvix_confirmation": "QVIX确认",
        "realized_vol_percentile": "实现波动率",
        "drawdown_pressure": "回撤压力",
        "market_breadth_pressure": "市场宽度",
        "turnover_stress": "成交压力",
    }
    scores = []
    for key, name in component_names.items():
        value = row.get(key)
        if pd.notna(value):
            scores.append((float(value), name))
    top = "、".join(name for _, name in sorted(scores, reverse=True)[:3])
    summary = f"{top}是当前风险温度的主要驱动；多因子合成后给出 {temp:.1f}/100。"
    return {
        "headline": f"市场风险温度 {temp:.1f}，处于{regime_cn}",
        "summary": summary,
        "posture": posture,
        "do_not_interpret_as": "这不是买卖建议，也不是官方波动率指数。",
    }

def _session_progress(value, trade_date: str) -> float:
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return 1.0
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Asia/Shanghai")
    day = pd.Timestamp(str(trade_date)[:10])
    minute = timestamp.hour * 60 + timestamp.minute + timestamp.second / 60.0
    morning_start, morning_end = 9 * 60 + 30, 11 * 60 + 30
    afternoon_start, afternoon_end = 13 * 60, 15 * 60
    if minute <= morning_start:
        elapsed = 0.0
    elif minute <= morning_end:
        elapsed = minute - morning_start
    elif minute < afternoon_start:
        elapsed = 120.0
    elif minute <= afternoon_end:
        elapsed = 120.0 + minute - afternoon_start
    else:
        elapsed = 240.0
    return max(0.05, min(1.0, elapsed / 240.0))


def compute_turnover(index_history: pd.DataFrame) -> pd.DataFrame:
    hs = index_history[index_history["symbol"] == "sh000300"].copy().sort_values("date")
    hs["volume"] = pd.to_numeric(hs["volume"], errors="coerce")
    source = hs.get("source", pd.Series("", index=hs.index)).fillna("").astype(str)
    realtime = source.str.contains("_RT")
    quote_time = pd.Series(pd.NA, index=hs.index, dtype="object")
    for column in ["source_quote_time", "quote_time", "fetch_time"]:
        if column in hs.columns:
            quote_time = quote_time.combine_first(hs[column])
    hs["session_progress"] = 1.0
    hs.loc[realtime, "session_progress"] = [
        _session_progress(value, trade_date)
        for value, trade_date in zip(quote_time.loc[realtime], hs.loc[realtime, "date"])
    ]
    hs["full_day_volume_estimate"] = hs["volume"] / hs["session_progress"]
    # Official rows retain the established formula exactly. Realtime rows use
    # a full-day-equivalent volume in the same rolling calculation so the
    # intraday estimate converges continuously to the official close value.
    hs["volume_mean_20_baseline"] = hs["full_day_volume_estimate"].rolling(20, min_periods=5).mean()
    hs["volume_ratio_20"] = hs["full_day_volume_estimate"] / hs["volume_mean_20_baseline"]
    hs["turnover_stress"] = hs["volume_ratio_20"].map(lambda x: clip((x - 0.8) / 1.2 * 100) if pd.notna(x) else 50)
    return hs[
        [
            "date", "volume_ratio_20", "turnover_stress", "session_progress",
            "full_day_volume_estimate", "volume_mean_20_baseline",
        ]
    ].rename(columns={"date": "trade_date"})


def _observed(row: pd.Series, name: str, fallback_value: str) -> bool:
    explicit = row.get(f"{name}_observed")
    if explicit is not None and not pd.isna(explicit):
        return bool(explicit)
    return pd.notna(row.get(fallback_value))


def _flag(row: pd.Series, name: str) -> bool:
    value = row.get(name)
    return bool(value) if value is not None and not pd.isna(value) else False


def _model_confidence_details(row: pd.Series) -> dict[str, object]:
    coverage_weight = 0.0
    quality_weight = 0.0
    missing: list[str] = []

    def add(name: str, weight: float, observed: bool, quality: float, missing_name: str) -> None:
        nonlocal coverage_weight, quality_weight
        if not observed:
            missing.append(missing_name)
            return
        coverage_weight += weight
        quality_weight += weight * max(0.0, min(1.0, quality))

    avix_quality = str(row.get("avix_quality", ""))
    avix_observed = _observed(row, "avix", "avix_clean")
    avix_usable = avix_observed and not avix_quality.startswith(("LOW", "BAD"))
    avix_flags = str(row.get("avix_quality_flags", "OK"))
    avix_score = observation_quality_score(
        observed=avix_usable,
        quality_flags=avix_flags,
        age_seconds=row.get("avix_age_seconds"),
        max_age_seconds=15 * 60,
        is_final=_flag(row, "avix_is_final"),
    )
    avix_factor_weight = sum(WEIGHTS[key] for key in ["avix_percentile_2y", "avix_zscore_1y", "avix_5d_change"])
    add("AVIX", avix_factor_weight, avix_usable, avix_score, "AVIX")

    qvix_quality = str(row.get("qvix_quality", ""))
    qvix_source = str(row.get("qvix_source", ""))
    qvix_flags = str(row.get("qvix_quality_flags", ""))
    uses_proxy = _flag(row, "is_proxy") or "QVIX_REALTIME_PROXY" in qvix_quality or "PROXY" in qvix_source or "PROXY" in qvix_flags
    uses_delayed = _flag(row, "is_delayed") or "QVIX_DELAYED" in qvix_quality or "DELAYED" in qvix_source or "DELAYED" in qvix_flags
    qvix_observed = _observed(row, "qvix", "qvix_close")
    qvix_score = observation_quality_score(
        observed=qvix_observed,
        quality_flags=qvix_flags or ("PROXY" if uses_proxy else "DELAYED" if uses_delayed else "OK"),
        age_seconds=row.get("qvix_age_seconds"),
        max_age_seconds=15 * 60,
        source_agreement=pd.to_numeric(row.get("qvix_source_agreement"), errors="coerce")
        if pd.notna(row.get("qvix_source_agreement"))
        else None,
        is_final=_flag(row, "is_final"),
    )
    add("QVIX", WEIGHTS["qvix_confirmation"], qvix_observed, qvix_score, "QVIX")
    if qvix_observed and uses_proxy:
        missing.append("QVIX_PROXY")
    elif qvix_observed and uses_delayed:
        missing.append("QVIX_DELAYED")

    index_flags = str(row.get("index_quality_flags", "OK"))
    index_quality = observation_quality_score(
        observed=True,
        quality_flags=index_flags,
        age_seconds=row.get("index_age_seconds"),
        max_age_seconds=15 * 60,
        source_agreement=pd.to_numeric(row.get("index_source_agreement"), errors="coerce")
        if pd.notna(row.get("index_source_agreement"))
        else None,
        is_final=_flag(row, "index_is_final"),
    )
    realized_observed = _observed(row, "realized_vol", "realized_vol_percentile")
    drawdown_observed = _observed(row, "drawdown", "drawdown_pressure")
    turnover_observed = _observed(row, "turnover", "turnover_stress")
    add("REALIZED_VOL", WEIGHTS["realized_vol_percentile"], realized_observed, index_quality, "REALIZED_VOL")
    add("DRAWDOWN", WEIGHTS["drawdown_pressure"], drawdown_observed, index_quality, "DRAWDOWN")

    breadth_quality = str(row.get("breadth_quality", ""))
    breadth_flags = str(row.get("breadth_quality_flags", ""))
    breadth_proxy = "WARN_BREADTH_PROXY" in breadth_quality or "PROXY" in breadth_flags
    breadth_observed = _observed(row, "breadth", "breadth_pressure")
    breadth_score = observation_quality_score(
        observed=breadth_observed,
        quality_flags=breadth_flags or ("PROXY" if breadth_proxy else "OK"),
        age_seconds=row.get("breadth_age_seconds"),
        max_age_seconds=15 * 60,
        source_agreement=pd.to_numeric(row.get("breadth_source_agreement"), errors="coerce")
        if pd.notna(row.get("breadth_source_agreement"))
        else None,
        is_final=_flag(row, "breadth_is_final"),
    )
    add("BREADTH", WEIGHTS["market_breadth_pressure"], breadth_observed, breadth_score, "BREADTH")
    if breadth_observed and breadth_proxy:
        missing.append("STOCK_BREADTH")
    add("TURNOVER", WEIGHTS["turnover_stress"], turnover_observed, index_quality, "TURNOVER")

    coverage = round(clip(coverage_weight * 100.0), 1)
    data_quality = round(clip(quality_weight / coverage_weight * 100.0), 1) if coverage_weight else 0.0
    confidence = round(clip(quality_weight * 100.0), 1)
    return {
        "score": confidence,
        "coverage_score": coverage,
        "data_quality_score": data_quality,
        "missing_components": "|".join(dict.fromkeys(missing)),
    }


def _model_confidence(row: pd.Series) -> tuple[float, str]:
    details = _model_confidence_details(row)
    return float(details["score"]), str(details["missing_components"])

def compute_risk_temperature(avix_clean: pd.DataFrame, qvix_validation: pd.DataFrame, realized: pd.DataFrame, drawdown: pd.DataFrame, breadth: pd.DataFrame, index_history: pd.DataFrame) -> pd.DataFrame:
    if avix_clean.empty and index_history.empty:
        return pd.DataFrame()
    if avix_clean.empty:
        hs = index_history[index_history["symbol"] == "sh000300"][["date", "close"]].rename(columns={"date": "trade_date"})
        df = hs.copy()
        df["avix_clean"] = np.nan
        avix_quality = "LOW_AVIX_UNAVAILABLE"
    else:
        avix_columns = [
            "trade_date", "avix_clean", "quality", "quality_flags",
            "source_quote_time", "fetch_time", "age_seconds", "observed",
            "is_final",
        ]
        df = avix_clean[[column for column in avix_columns if column in avix_clean.columns]].rename(
            columns={
                "quality": "avix_quality",
                "quality_flags": "avix_quality_flags",
                "source_quote_time": "avix_source_quote_time",
                "fetch_time": "avix_fetch_time",
                "age_seconds": "avix_age_seconds",
                "observed": "avix_observed",
                "is_final": "avix_is_final",
            }
        ).copy()
        avix_quality = None
    df = df.sort_values("trade_date")
    min_periods = _PERCENTILE_MIN_PERIODS
    df["avix_percentile_2y"] = df["avix_clean"].rolling(504, min_periods=min_periods).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False).fillna(50)
    z = (df["avix_clean"] - df["avix_clean"].rolling(252, min_periods=min_periods).mean()) / df["avix_clean"].rolling(252, min_periods=min_periods).std()
    df["avix_zscore_1y"] = z.map(lambda x: clip(50 + 20 * x) if pd.notna(x) else 50)
    chg5 = df["avix_clean"] / df["avix_clean"].shift(5) - 1
    df["avix_5d_change"] = chg5.map(lambda x: clip(50 + 200 * x) if pd.notna(x) else 50)
    breadth_for_merge = breadth.copy() if not breadth.empty else pd.DataFrame()
    if not breadth_for_merge.empty and "quality" in breadth_for_merge.columns:
        breadth_for_merge = breadth_for_merge.rename(columns={"quality": "breadth_quality"})
    proxy_breadth = compute_index_breadth_proxy(index_history)
    if not proxy_breadth.empty:
        if "quality" in proxy_breadth.columns:
            proxy_breadth = proxy_breadth.rename(columns={"quality": "breadth_quality"})
        if breadth_for_merge.empty:
            breadth_for_merge = proxy_breadth
        else:
            # Prefer real stock breadth (OK*) over index proxy on the same trade_date.
            stock = breadth_for_merge.copy()
            if "breadth_quality" in stock.columns:
                stock_ok = stock["breadth_quality"].astype(str).str.startswith("OK")
                if "valid_count" in stock.columns:
                    counts = pd.to_numeric(stock["valid_count"], errors="coerce").fillna(0)
                    stock_ok = stock_ok & (counts >= 1000)
                stock_prefer = stock.loc[stock_ok]
            else:
                stock_prefer = stock
            breadth_for_merge = (
                pd.concat([proxy_breadth, stock_prefer], ignore_index=True)
                .drop_duplicates("trade_date", keep="last")
                .sort_values("trade_date")
            )
    qvix_cols = [
        "trade_date", "qvix_confirmation", "qvix_close", "quality",
        "qvix_source", "qvix_quote_time", "qvix_delay_minutes", "age_seconds",
        "is_proxy", "is_delayed", "sample_size", "observed", "quality_flags",
        "is_final", "secondary_source", "source_agreement", "source_value_delta",
        "qvix_replica", "qvix_replica_quality", "qvix_replica_method",
    ]
    available_qvix_cols = [col for col in qvix_cols if col in qvix_validation.columns]
    qvix_for_merge = (
        qvix_validation[available_qvix_cols].rename(columns={
            "quality": "qvix_quality",
            "source_agreement": "qvix_source_agreement",
            "secondary_source": "qvix_secondary_source",
        })
        if not qvix_validation.empty
        else pd.DataFrame()
    )
    for extra in [qvix_for_merge, realized, drawdown, breadth_for_merge, compute_turnover(index_history)]:
        if not extra.empty:
            df = df.merge(extra, on="trade_date", how="left", suffixes=("", "_extra"))
    df["avix_observed"] = df["avix_clean"].notna()
    df["qvix_observed"] = df.get("qvix_close", pd.Series(index=df.index, dtype=float)).notna()
    df["realized_vol_observed"] = df.get("realized_vol_percentile", pd.Series(index=df.index, dtype=float)).notna()
    df["drawdown_observed"] = df.get("drawdown_pressure", pd.Series(index=df.index, dtype=float)).notna()
    df["breadth_observed"] = df.get("breadth_pressure", pd.Series(index=df.index, dtype=float)).notna()
    df["turnover_observed"] = df.get("turnover_stress", pd.Series(index=df.index, dtype=float)).notna()
    rename_quality = {
        "quality_flags": "qvix_quality_flags",
        "age_seconds": "qvix_age_seconds",
        "source_agreement": "breadth_source_agreement",
    }
    for source_column, target_column in rename_quality.items():
        if source_column in df.columns and target_column not in df.columns:
            df[target_column] = df[source_column]
    if "quality_flags_extra" in df.columns:
        df["breadth_quality_flags"] = df["quality_flags_extra"]
    if "age_seconds_extra" in df.columns:
        df["breadth_age_seconds"] = df["age_seconds_extra"]
    if "is_final_extra" in df.columns:
        df["breadth_is_final"] = df["is_final_extra"]
    index_meta = index_history.copy()
    if not index_meta.empty and "quality_flags" in index_meta.columns:
        index_meta["trade_date"] = pd.to_datetime(index_meta["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        index_meta = index_meta[index_meta["symbol"].astype(str).isin(["sh000300", "sh000001"])]
        index_meta = index_meta.groupby("trade_date", as_index=False).agg(
            index_quality_flags=("quality_flags", lambda values: "|".join(sorted(set(
                flag for value in values.dropna().astype(str) for flag in value.split("|") if flag and flag != "OK"
            ))) or "OK"),
            index_age_seconds=("age_seconds", "max") if "age_seconds" in index_meta.columns else ("trade_date", lambda _x: None),
            index_source_agreement=("source_agreement", "min") if "source_agreement" in index_meta.columns else ("trade_date", lambda _x: None),
            index_is_final=("is_final", "all") if "is_final" in index_meta.columns else ("trade_date", lambda _x: False),
        )
        df = df.merge(index_meta, on="trade_date", how="left")
    df["qvix_confirmation"] = df["qvix_confirmation"].fillna(50)
    df["realized_vol_percentile"] = df["realized_vol_percentile"].fillna(50)
    df["drawdown_pressure"] = df["drawdown_pressure"].fillna(50)
    df["market_breadth_pressure"] = df.get("breadth_pressure", pd.Series(index=df.index, dtype=float)).ffill().fillna(50)
    df["turnover_stress"] = df["turnover_stress"].fillna(50)
    temp = sum(df[k] * w for k, w in WEIGHTS.items())
    df["risk_temperature"] = temp.map(lambda x: round(clip(x), 1))
    regimes = df["risk_temperature"].map(regime_for)
    df["regime"] = regimes.map(lambda x: x[0])
    df["regime_cn"] = regimes.map(lambda x: x[1])
    def q(row):
        flags = [avix_quality, row.get("avix_quality"), row.get("qvix_quality"), row.get("breadth_quality")]
        if pd.isna(row.get("qvix_close", np.nan)):
            flags.append("WARN_QVIX_MISSING")
        if pd.isna(row.get("breadth_pressure", np.nan)):
            flags.append("WARN_BREADTH_MISSING")
        return merge_quality([f for f in flags if isinstance(f, str)])
    df["quality"] = df.apply(q, axis=1)
    confidence = df.apply(_model_confidence_details, axis=1)
    df["model_confidence"] = confidence.map(lambda item: item["score"])
    df["model_coverage_score"] = confidence.map(lambda item: item["coverage_score"])
    df["model_data_quality_score"] = confidence.map(lambda item: item["data_quality_score"])
    df["model_missing_components"] = confidence.map(lambda item: item["missing_components"])
    return df
