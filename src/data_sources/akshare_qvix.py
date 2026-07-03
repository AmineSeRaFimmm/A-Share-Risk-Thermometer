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
    out["source"] = "AKSHARE_OPTBBS_QVIX"
    out["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    return out
