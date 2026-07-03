from __future__ import annotations
from datetime import date
import pandas as pd
from src.utils.dates import third_friday

def trading_days_from_index(index_df: pd.DataFrame) -> list[date]:
    if index_df.empty:
        return []
    return sorted(pd.to_datetime(index_df["date"]).dt.date.unique())

def get_expiry_date(month: str, trading_days: set[date]) -> date:
    y, m = [int(x) for x in month.split("-")]
    expiry = third_friday(y, m)
    if expiry in trading_days:
        return expiry
    later = sorted(d for d in trading_days if d > expiry)
    return later[0] if later else expiry
