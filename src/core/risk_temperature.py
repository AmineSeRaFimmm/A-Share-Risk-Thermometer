from __future__ import annotations
import numpy as np
import pandas as pd
from src.core.breadth import compute_index_breadth_proxy
from src.utils.config import load_regimes, load_thresholds, load_weights
from src.utils.quality import clip, merge_quality

WEIGHTS = load_weights()
REGIMES = load_regimes()
_THRESHOLDS = load_thresholds()
# Keep warmup at 20 for bit-identical history until methodology PR raises this to config value.
_PERCENTILE_MIN_PERIODS = 20
_PERCENTILE_MIN_PERIODS_CONFIG = int(_THRESHOLDS["min_history_days_for_percentile"])


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

def compute_turnover(index_history: pd.DataFrame) -> pd.DataFrame:
    hs = index_history[index_history["symbol"] == "sh000300"].copy().sort_values("date")
    hs["volume"] = pd.to_numeric(hs["volume"], errors="coerce")
    hs["volume_ratio_20"] = hs["volume"] / hs["volume"].rolling(20, min_periods=5).mean()
    hs["turnover_stress"] = hs["volume_ratio_20"].map(lambda x: clip((x - 0.8) / 1.2 * 100) if pd.notna(x) else 50)
    return hs[["date", "volume_ratio_20", "turnover_stress"]].rename(columns={"date": "trade_date"})


def _model_confidence(row: pd.Series) -> tuple[float, str]:
    available_weight = 0.0
    missing = []
    avix_quality = str(row.get("avix_quality", ""))
    if pd.notna(row.get("avix_clean")) and not avix_quality.startswith("LOW") and not avix_quality.startswith("BAD"):
        available_weight += WEIGHTS["avix_percentile_2y"] + WEIGHTS["avix_zscore_1y"] + WEIGHTS["avix_5d_change"]
    else:
        missing.append("AVIX")
    if pd.notna(row.get("qvix_close")):
        available_weight += WEIGHTS["qvix_confirmation"]
    else:
        missing.append("QVIX")
    if pd.notna(row.get("realized_vol_percentile")):
        available_weight += WEIGHTS["realized_vol_percentile"]
    else:
        missing.append("REALIZED_VOL")
    if pd.notna(row.get("drawdown_pressure")):
        available_weight += WEIGHTS["drawdown_pressure"]
    else:
        missing.append("DRAWDOWN")
    breadth_quality = str(row.get("breadth_quality", ""))
    if pd.notna(row.get("breadth_pressure")):
        uses_proxy = "WARN_BREADTH_PROXY" in breadth_quality
        available_weight += WEIGHTS["market_breadth_pressure"] * (0.6 if uses_proxy else 1.0)
        if uses_proxy:
            missing.append("STOCK_BREADTH")
    else:
        missing.append("BREADTH")
    if pd.notna(row.get("turnover_stress")):
        available_weight += WEIGHTS["turnover_stress"]
    else:
        missing.append("TURNOVER")
    return round(clip(available_weight * 100), 1), "|".join(missing)

def compute_risk_temperature(avix_clean: pd.DataFrame, qvix_validation: pd.DataFrame, realized: pd.DataFrame, drawdown: pd.DataFrame, breadth: pd.DataFrame, index_history: pd.DataFrame) -> pd.DataFrame:
    if avix_clean.empty and index_history.empty:
        return pd.DataFrame()
    if avix_clean.empty:
        hs = index_history[index_history["symbol"] == "sh000300"][["date", "close"]].rename(columns={"date": "trade_date"})
        df = hs.copy()
        df["avix_clean"] = np.nan
        avix_quality = "LOW_AVIX_UNAVAILABLE"
    else:
        df = avix_clean[["trade_date", "avix_clean", "quality"]].rename(columns={"quality": "avix_quality"}).copy()
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
            breadth_for_merge = (
                pd.concat([proxy_breadth, breadth_for_merge], ignore_index=True)
                .drop_duplicates("trade_date", keep="last")
                .sort_values("trade_date")
            )
    qvix_cols = [
        "trade_date", "qvix_confirmation", "qvix_close", "quality",
        "qvix_replica", "qvix_replica_quality", "qvix_replica_method",
    ]
    available_qvix_cols = [col for col in qvix_cols if col in qvix_validation.columns]
    qvix_for_merge = qvix_validation[available_qvix_cols].rename(columns={"quality": "qvix_quality"}) if not qvix_validation.empty else pd.DataFrame()
    for extra in [qvix_for_merge, realized, drawdown, breadth_for_merge, compute_turnover(index_history)]:
        if not extra.empty:
            df = df.merge(extra, on="trade_date", how="left", suffixes=("", "_extra"))
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
    confidence = df.apply(_model_confidence, axis=1)
    df["model_confidence"] = confidence.map(lambda x: x[0])
    df["model_missing_components"] = confidence.map(lambda x: x[1])
    return df
