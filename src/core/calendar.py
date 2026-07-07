from __future__ import annotations
from datetime import date
import pandas as pd
from src.utils.dates import third_friday
from src.utils.dates import today_cn

def trading_days_from_index(index_df: pd.DataFrame) -> list[date]:
    if index_df.empty:
        return []
    return sorted(pd.to_datetime(index_df["date"]).dt.date.unique())

def trading_days_from_akshare() -> list[date]:
    try:
        import akshare as ak

        cal = ak.tool_trade_date_hist_sina()
    except Exception:
        return []
    if cal is None or cal.empty:
        return []
    date_col = next((c for c in cal.columns if "date" in str(c).lower() or "日期" in str(c)), cal.columns[0])
    return sorted(pd.to_datetime(cal[date_col], errors="coerce").dropna().dt.date.unique())

def merged_trading_days(index_df: pd.DataFrame) -> list[date]:
    return sorted(set(trading_days_from_index(index_df)) | set(trading_days_from_akshare()))

def current_realtime_trade_date(index_df: pd.DataFrame, current: date | None = None) -> str:
    current = current or today_cn()
    days = [d for d in merged_trading_days(index_df) if d <= current]
    if not days:
        days = [d for d in trading_days_from_index(index_df) if d <= current]
    if not days:
        raise ValueError("No trading calendar available for realtime AVIX")
    if current in set(days):
        return current.isoformat()
    return max(days).isoformat()

def get_expiry_date(month: str, trading_days: set[date]) -> date:
    y, m = [int(x) for x in month.split("-")]
    expiry = third_friday(y, m)
    if expiry in trading_days:
        return expiry
    later = sorted(d for d in trading_days if d > expiry)
    return later[0] if later else expiry
