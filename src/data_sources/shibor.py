from __future__ import annotations
from datetime import datetime
import pandas as pd

TENOR_DAYS = {"O/N": 1, "1W": 7, "2W": 14, "1M": 30, "3M": 90, "6M": 180, "1Y": 365}

def fetch_shibor() -> pd.DataFrame:
    import akshare as ak

    if hasattr(ak, "rate_interbank"):
        raw = ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="1月")
        if raw is not None and not raw.empty:
            date_col = next((c for c in raw.columns if "日期" in str(c) or "date" in str(c).lower()), raw.columns[0])
            rate_col = next((c for c in raw.columns if "利率" in str(c) or "rate" in str(c).lower()), raw.columns[-1])
            df = raw[[date_col, rate_col]].rename(columns={date_col: "trade_date", rate_col: "rate"})
            df["tenor_days"] = 30
            df["source"] = "AKSHARE_SHIBOR_1M"
            df["fetch_time"] = datetime.now().isoformat(timespec="seconds")
            df["quality"] = "OK"
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date.astype(str)
            df["rate"] = pd.to_numeric(df["rate"], errors="coerce") / 100.0
            return df[["trade_date", "tenor_days", "rate", "source", "fetch_time", "quality"]]
    return pd.DataFrame()

def fallback_rate_curve(trade_dates: list[str], rate: float = 0.02) -> pd.DataFrame:
    rows = []
    for d in trade_dates:
        for tenor in [7, 14, 30, 90, 180, 365]:
            rows.append({
                "trade_date": d,
                "tenor_days": tenor,
                "rate": rate,
                "source": "FALLBACK_CONSTANT_SHIBOR",
                "fetch_time": datetime.now().isoformat(timespec="seconds"),
                "quality": "WARN_RATE_STALE",
            })
    return pd.DataFrame(rows)
