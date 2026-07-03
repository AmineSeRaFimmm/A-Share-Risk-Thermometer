from __future__ import annotations
from datetime import datetime
import pandas as pd

def fetch_a_breadth_snapshot() -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_zh_a_spot_em()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    df["source"] = "AKSHARE_EASTMONEY_A_SPOT"
    return df

def summarize_breadth(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame([{"trade_date": trade_date, "quality": "WARN_BREADTH_MISSING"}])
    price_col = next((c for c in df.columns if str(c) in ["最新价", "price", "最新"]), None)
    pct_col = next((c for c in df.columns if str(c) in ["涨跌幅", "pct_chg", "涨跌幅%"]), None)
    amount_col = next((c for c in df.columns if str(c) in ["成交额", "amount"]), None)
    turnover_col = next((c for c in df.columns if str(c) in ["换手率", "turnover"]), None)
    volume_ratio_col = next((c for c in df.columns if str(c) in ["量比", "volume_ratio"]), None)
    if pct_col is None:
        return pd.DataFrame([{"trade_date": trade_date, "quality": "WARN_BREADTH_MISSING"}])
    work = df.copy()
    if price_col:
        valid = pd.to_numeric(work[price_col], errors="coerce") > 0
    else:
        valid = pd.Series(True, index=work.index)
    pct = pd.to_numeric(work[pct_col], errors="coerce")
    valid_count = int(valid.sum())
    denom = max(valid_count, 1)
    out = {
        "trade_date": trade_date,
        "valid_count": valid_count,
        "advancing_count": int(((pct > 0) & valid).sum()),
        "declining_count": int(((pct < 0) & valid).sum()),
        "big_down_count": int(((pct <= -5) & valid).sum()),
        "limit_down_count": int(((pct <= -9.5) & valid).sum()),
        "advancing_ratio": float(((pct > 0) & valid).sum() / denom),
        "decline_ratio": float(((pct < 0) & valid).sum() / denom),
        "big_down_ratio": float(((pct <= -5) & valid).sum() / denom),
        "limit_down_ratio": float(((pct <= -9.5) & valid).sum() / denom),
        "total_amount": float(pd.to_numeric(work[amount_col], errors="coerce").sum()) if amount_col else None,
        "turnover_median": float(pd.to_numeric(work[turnover_col], errors="coerce").median()) if turnover_col else None,
        "volume_ratio_median": float(pd.to_numeric(work[volume_ratio_col], errors="coerce").median()) if volume_ratio_col else None,
        "quality": "OK",
    }
    return pd.DataFrame([out])
