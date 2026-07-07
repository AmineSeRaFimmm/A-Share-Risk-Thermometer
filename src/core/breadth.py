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


def compute_index_breadth_proxy(index_history: pd.DataFrame) -> pd.DataFrame:
    """Fallback breadth proxy from broad index participation.

    This is deliberately marked as a proxy because it is not stock-level A-share
    breadth. It is still better than silently treating missing breadth as neutral.
    """
    if index_history.empty or not {"date", "symbol", "close"}.issubset(index_history.columns):
        return pd.DataFrame()
    work = index_history[["date", "symbol", "close"]].copy()
    work["trade_date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.dropna(subset=["trade_date", "symbol", "close"]).sort_values(["symbol", "trade_date"])
    work["ret1"] = work.groupby("symbol")["close"].pct_change()
    rows = []
    for trade_date, day in work.dropna(subset=["ret1"]).groupby("trade_date"):
        valid = day["ret1"].dropna()
        if len(valid) < 3:
            continue
        advancing_ratio = float((valid > 0).mean())
        decline_ratio = float((valid < 0).mean())
        big_down_ratio = float((valid <= -0.03).mean())
        limit_down_ratio = float((valid <= -0.06).mean())
        breadth_pressure = (
            50 * (1 - advancing_ratio)
            + 30 * clip(big_down_ratio / 0.50, 0, 1)
            + 20 * clip(limit_down_ratio / 0.30, 0, 1)
        )
        rows.append({
            "trade_date": trade_date,
            "advancing_ratio": advancing_ratio,
            "decline_ratio": decline_ratio,
            "big_down_ratio": big_down_ratio,
            "limit_down_ratio": limit_down_ratio,
            "breadth_pressure": float(breadth_pressure),
            "quality": "WARN_BREADTH_PROXY",
        })
    return pd.DataFrame(rows)

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
