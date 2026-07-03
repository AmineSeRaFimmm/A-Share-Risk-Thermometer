from __future__ import annotations
import pandas as pd
from src.utils.quality import clip

def compute_breadth_pressure(breadth_history: pd.DataFrame) -> pd.DataFrame:
    if breadth_history.empty:
        return pd.DataFrame()
    df = breadth_history.copy().sort_values("trade_date")
    for col in ["advancing_ratio", "big_down_ratio", "limit_down_ratio"]:
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    df["breadth_pressure"] = (
        50 * (1 - df["advancing_ratio"].fillna(0.5))
        + 30 * df["big_down_ratio"].fillna(0).map(lambda x: clip(x / 0.08, 0, 1))
        + 20 * df["limit_down_ratio"].fillna(0).map(lambda x: clip(x / 0.02, 0, 1))
    )
    return df[["trade_date", "advancing_ratio", "decline_ratio", "big_down_ratio", "limit_down_ratio", "breadth_pressure", "quality"]]

def drop_legacy_synthetic_breadth(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    work = df.copy()
    for col in ["advancing_ratio", "decline_ratio", "big_down_ratio", "limit_down_ratio"]:
        if col not in work.columns:
            work[col] = pd.NA
        work[col] = pd.to_numeric(work[col], errors="coerce")
    quality = work.get("quality", pd.Series("", index=work.index)).astype(str)
    legacy = (
        quality.eq("WARN_BREADTH_MISSING")
        & work["advancing_ratio"].eq(0.5)
        & work["decline_ratio"].eq(0.5)
        & work["big_down_ratio"].eq(0.0)
        & work["limit_down_ratio"].eq(0.0)
    )
    return work.loc[~legacy].copy()
