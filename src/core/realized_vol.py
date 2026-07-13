from __future__ import annotations
import numpy as np
import pandas as pd
from src.utils.config import load_thresholds

_MIN_PERIODS = int(load_thresholds()["min_history_days_for_percentile"])


def compute_realized_vol(index_history: pd.DataFrame) -> pd.DataFrame:
    if index_history.empty:
        return pd.DataFrame()
    hs = index_history[index_history["symbol"] == "sh000300"].copy()
    hs["close"] = pd.to_numeric(hs["close"], errors="coerce")
    hs = hs.sort_values("date")
    hs["ret"] = np.log(hs["close"] / hs["close"].shift(1))
    hs["rv20"] = hs["ret"].rolling(20).std() * np.sqrt(252) * 100
    hs["rv60"] = hs["ret"].rolling(60).std() * np.sqrt(252) * 100
    hs["realized_vol_percentile"] = hs["rv20"].rolling(504, min_periods=_MIN_PERIODS).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100,
        raw=False,
    )
    return hs[["date", "rv20", "rv60", "realized_vol_percentile"]].rename(columns={"date": "trade_date"})
