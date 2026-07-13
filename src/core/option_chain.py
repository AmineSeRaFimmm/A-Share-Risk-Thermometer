from __future__ import annotations
import pandas as pd
from src.core.calendar import get_expiry_date

def build_daily_option_chain(master: pd.DataFrame, option_frames: list[pd.DataFrame], trading_days: set) -> pd.DataFrame:
    if master.empty or not option_frames:
        return pd.DataFrame()
    raw = pd.concat(option_frames, ignore_index=True)
    trade_dates = pd.to_datetime(raw["date"], errors="coerce")
    raw["trade_date"] = trade_dates.dt.date
    expiry_map = {m: get_expiry_date(m, trading_days) for m in raw["month"].dropna().unique()}
    raw["expiry_date"] = raw["month"].map(expiry_map)
    expiry_dates = pd.to_datetime(raw["expiry_date"], errors="coerce")
    raw["dte"] = (expiry_dates - trade_dates).dt.days
    for col in ["open", "high", "low", "close", "volume", "strike"]:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw["price_raw"] = raw["close"]
    raw["valid_price"] = raw["close"] > 0
    raw["valid_volume"] = raw["volume"].fillna(0) >= 0
    out = raw[(raw["strike"] > 0) & (raw["dte"] >= 7) & (raw["dte"] <= 180)].copy()
    out["trade_date"] = out["trade_date"].astype(str)
    out["expiry_date"] = out["expiry_date"].astype(str)
    cols = [
        "trade_date", "contract", "month", "cp", "strike", "expiry_date", "dte",
        "open", "high", "low", "close", "volume", "price_raw",
        "valid_price", "valid_volume", "source"
    ]
    return out[cols].sort_values(["trade_date", "expiry_date", "strike", "cp"])
