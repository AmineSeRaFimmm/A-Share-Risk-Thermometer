from __future__ import annotations
from datetime import datetime
import pandas as pd

TENOR_DAYS = {"O/N": 1, "1W": 7, "2W": 14, "1M": 30, "3M": 90, "6M": 180, "1Y": 365}
AK_INDICATORS = {
    7: "1周",
    14: "2周",
    30: "1月",
    90: "3月",
    180: "6月",
    365: "1年",
}

def fetch_shibor() -> pd.DataFrame:
    import akshare as ak

    frames = []
    if not hasattr(ak, "rate_interbank"):
        return pd.DataFrame()
    for tenor_days, indicator in AK_INDICATORS.items():
        raw = ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator=indicator)
        if raw is None or raw.empty:
            continue
        date_col = next((c for c in raw.columns if "日期" in str(c) or "报告日" in str(c) or "date" in str(c).lower()), raw.columns[0])
        rate_col = next((c for c in raw.columns if "利率" in str(c) or "rate" in str(c).lower()), raw.columns[-1])
        df = raw[[date_col, rate_col]].rename(columns={date_col: "trade_date", rate_col: "rate"})
        df["tenor_days"] = tenor_days
        df["source"] = f"AKSHARE_SHIBOR_{indicator}"
        df["fetch_time"] = datetime.now().isoformat(timespec="seconds")
        df["quality"] = "OK"
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date.astype(str)
        df["rate"] = pd.to_numeric(df["rate"], errors="coerce") / 100.0
        frames.append(df[["trade_date", "tenor_days", "rate", "source", "fetch_time", "quality"]])
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).dropna(subset=["trade_date", "tenor_days", "rate"])
