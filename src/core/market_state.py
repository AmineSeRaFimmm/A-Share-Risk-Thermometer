from __future__ import annotations

import pandas as pd


INTRADAY = "NOWCAST"
CLOSE_PENDING = "CLOSE_PENDING"
ESTIMATED_CLOSE = "ESTIMATED_CLOSE"
OFFICIAL_CLOSE = "OFFICIAL_CLOSE"

MODE_CN = {
    INTRADAY: "盘中估算",
    CLOSE_PENDING: "收盘确认中",
    ESTIMATED_CLOSE: "估算收盘",
    OFFICIAL_CLOSE: "收盘正式",
}


def estimated_temperature_mode(valuation_time) -> tuple[str, str]:
    timestamp = pd.to_datetime(valuation_time, errors="coerce")
    if pd.isna(timestamp):
        return INTRADAY, MODE_CN[INTRADAY]
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Asia/Shanghai")
    else:
        timestamp = timestamp.tz_convert("Asia/Shanghai")
    minute = timestamp.hour * 60 + timestamp.minute
    if minute < 15 * 60:
        mode = INTRADAY
    elif minute < 15 * 60 + 16:
        mode = CLOSE_PENDING
    else:
        mode = ESTIMATED_CLOSE
    return mode, MODE_CN[mode]
