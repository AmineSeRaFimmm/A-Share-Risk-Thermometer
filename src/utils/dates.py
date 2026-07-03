from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import pandas as pd

CN_TZ = timezone(timedelta(hours=8))

def now_cn() -> datetime:
    return datetime.now(CN_TZ)

def today_cn() -> date:
    return now_cn().date()

def to_date(value) -> date:
    return pd.to_datetime(value).date()

def ymd(value) -> str:
    return to_date(value).isoformat()

def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    first_friday_offset = (4 - d.weekday()) % 7
    return d + timedelta(days=first_friday_offset + 14)

def month_iter(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y += 1
            m = 1
