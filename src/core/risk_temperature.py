from __future__ import annotations
import numpy as np
import pandas as pd
from src.utils.quality import clip, merge_quality

WEIGHTS = {
    "avix_percentile_2y": 0.28,
    "avix_zscore_1y": 0.14,
    "avix_5d_change": 0.08,
    "qvix_confirmation": 0.12,
    "realized_vol_percentile": 0.12,
    "drawdown_pressure": 0.12,
    "market_breadth_pressure": 0.10,
    "turnover_stress": 0.04,
}

REGIMES = [
    (20, "CALM", "平静"),
    (40, "NORMAL", "正常"),
    (60, "CAUTION", "警戒"),
    (75, "HIGH_RISK", "高风险"),
    (90, "PANIC", "恐慌区"),
    (101, "EXTREME_PANIC", "极端恐慌"),
]

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
    df["avix_percentile_2y"] = df["avix_clean"].rolling(504, min_periods=20).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False).fillna(50)
    z = (df["avix_clean"] - df["avix_clean"].rolling(252, min_periods=20).mean()) / df["avix_clean"].rolling(252, min_periods=20).std()
    df["avix_zscore_1y"] = z.map(lambda x: clip(50 + 20 * x) if pd.notna(x) else 50)
    chg5 = df["avix_clean"] / df["avix_clean"].shift(5) - 1
    df["avix_5d_change"] = chg5.map(lambda x: clip(50 + 200 * x) if pd.notna(x) else 50)
    for extra in [qvix_validation[["trade_date", "qvix_confirmation", "qvix_close", "quality"]] if not qvix_validation.empty else pd.DataFrame(),
                  realized, drawdown, breadth, compute_turnover(index_history)]:
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
        flags = [avix_quality, row.get("avix_quality"), row.get("quality")]
        if pd.isna(row.get("qvix_close", np.nan)):
            flags.append("WARN_QVIX_MISSING")
        if pd.isna(row.get("breadth_pressure", np.nan)):
            flags.append("WARN_BREADTH_MISSING")
        return merge_quality([f for f in flags if isinstance(f, str)])
    df["quality"] = df.apply(q, axis=1)
    return df
