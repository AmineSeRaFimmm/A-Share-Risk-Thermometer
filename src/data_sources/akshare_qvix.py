from __future__ import annotations
from datetime import datetime
import pandas as pd


def fetch_qvix() -> pd.DataFrame:
    import akshare as ak

    df = ak.index_option_300index_qvix()
    if df is None or df.empty:
        return pd.DataFrame()
    rename = {}
    for c in df.columns:
        s = str(c).lower()
        if "date" in s or "日期" in str(c):
            rename[c] = "date"
        elif "open" in s or "开" in str(c):
            rename[c] = "open"
        elif "high" in s or "高" in str(c):
            rename[c] = "high"
        elif "low" in s or "低" in str(c):
            rename[c] = "low"
        elif "close" in s or "收" in str(c):
            rename[c] = "close"
    df = df.rename(columns=rename)
    for col in ["date", "open", "high", "low", "close"]:
        if col not in df.columns:
            df[col] = pd.NA
    out = df[["date", "open", "high", "low", "close"]].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date.astype(str)
    for col in ["open", "high", "low", "close"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
        out.loc[out[col] <= 0, col] = pd.NA
    out["source"] = "AKSHARE_OPTBBS_QVIX"
    out["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    return out


def merge_qvix_cache(fresh: pd.DataFrame, cached: pd.DataFrame) -> pd.DataFrame:
    """Merge fresh QVIX with cache, preferring non-null close values.

    Upstream sometimes returns trailing date rows with empty OHLC; keep prior
    good closes instead of overwriting them with NaN.
    """
    if fresh is None or fresh.empty:
        return cached.copy() if cached is not None and not cached.empty else pd.DataFrame()
    if cached is None or cached.empty:
        return fresh.copy()
    cols = ["date", "open", "high", "low", "close", "source", "fetch_time"]
    for frame in (fresh, cached):
        for col in cols:
            if col not in frame.columns:
                frame[col] = pd.NA
    left = cached[cols].copy()
    right = fresh[cols].copy()
    left["date"] = pd.to_datetime(left["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    right["date"] = pd.to_datetime(right["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    merged = left.merge(right, on="date", how="outer", suffixes=("_old", "_new"))
    out = pd.DataFrame({"date": merged["date"]})
    for col in ["open", "high", "low", "close"]:
        new = pd.to_numeric(merged[f"{col}_new"], errors="coerce")
        old = pd.to_numeric(merged[f"{col}_old"], errors="coerce")
        out[col] = new.combine_first(old)
    out["source"] = merged["source_new"].fillna(merged["source_old"])
    out["fetch_time"] = merged["fetch_time_new"].fillna(merged["fetch_time_old"])
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
