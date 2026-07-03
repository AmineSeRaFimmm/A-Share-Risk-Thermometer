from __future__ import annotations
from datetime import datetime
import pandas as pd

def fetch_index_daily(symbol: str) -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_zh_index_daily(symbol=symbol)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={c: c.lower() for c in df.columns})
    needed = ["date", "open", "close", "high", "low", "volume"]
    for col in needed:
        if col not in df.columns:
            df[col] = pd.NA
    out = df[needed].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date.astype(str)
    out["symbol"] = symbol
    out["source"] = "AKSHARE_SINA_INDEX"
    out["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    return out
