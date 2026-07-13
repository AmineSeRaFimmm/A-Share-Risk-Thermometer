from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

from src.utils.config import load_data_sources
from src.utils.retry import retry_call

TENOR_DAYS = {"O/N": 1, "1W": 7, "2W": 14, "1M": 30, "3M": 90, "6M": 180, "1Y": 365}
AK_INDICATORS = {
    7: "1周",
    14: "2周",
    30: "1月",
    90: "3月",
    180: "6月",
    365: "1年",
}


def _fetch_one_tenor(tenor_days: int, indicator: str) -> pd.DataFrame:
    import akshare as ak

    raw = ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator=indicator)
    if raw is None or raw.empty:
        return pd.DataFrame()
    date_col = next((c for c in raw.columns if "日期" in str(c) or "报告日" in str(c) or "date" in str(c).lower()), raw.columns[0])
    rate_col = next((c for c in raw.columns if "利率" in str(c) or "rate" in str(c).lower()), raw.columns[-1])
    df = raw[[date_col, rate_col]].rename(columns={date_col: "trade_date", rate_col: "rate"})
    df["tenor_days"] = tenor_days
    df["source"] = f"AKSHARE_SHIBOR_{indicator}"
    df["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    df["quality"] = "OK"
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date.astype(str)
    df["rate"] = pd.to_numeric(df["rate"], errors="coerce") / 100.0
    return df[["trade_date", "tenor_days", "rate", "source", "fetch_time", "quality"]]


def fetch_shibor(max_workers: int = 4) -> pd.DataFrame:
    import akshare as ak

    if not hasattr(ak, "rate_interbank"):
        return pd.DataFrame()

    cfg = load_data_sources()
    retry_times = int(cfg["retry_times"])
    retry_sleep = float(cfg["retry_sleep_seconds"])
    frames: list[pd.DataFrame] = []

    def work(item: tuple[int, str]) -> pd.DataFrame:
        tenor_days, indicator = item
        return retry_call(
            lambda: _fetch_one_tenor(tenor_days, indicator),
            times=retry_times,
            sleep_seconds=retry_sleep,
        )

    items = list(AK_INDICATORS.items())
    workers = max(1, min(max_workers, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, item) for item in items]
        for future in as_completed(futures):
            try:
                df = future.result()
            except Exception:
                continue
            if df is not None and not df.empty:
                frames.append(df)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).dropna(subset=["trade_date", "tenor_days", "rate"])
