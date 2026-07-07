#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.storage.paths import CALCULATED, NORMALIZED
from src.storage.csv_store import read_csv
from src.core.calendar import current_realtime_trade_date

def fail(message: str) -> None:
    print(message)
    sys.exit(1)

def number(row, name: str):
    return pd.to_numeric(pd.Series([row.get(name)]), errors="coerce").iloc[0]

def main() -> None:
    path = CALCULATED / "avix_realtime_mid.csv"
    if not path.exists():
        fail("avix_realtime_mid.csv missing")
    df = pd.read_csv(path)
    if df.empty:
        fail("avix_realtime_mid.csv empty")
    if not {"avix_mid", "quality"}.issubset(df.columns):
        fail("avix_realtime_mid.csv missing avix_mid or quality")

    row = df.sort_values("valuation_time").iloc[-1] if "valuation_time" in df.columns else df.iloc[-1]
    index_history = read_csv(NORMALIZED / "index_history.csv")
    hs300 = index_history[index_history["symbol"] == "sh000300"].copy() if not index_history.empty and "symbol" in index_history.columns else pd.DataFrame()
    expected_trade_date = current_realtime_trade_date(hs300)
    actual_trade_date = str(row.get("trade_date", ""))[:10]
    if actual_trade_date != expected_trade_date:
        fail(f"realtime AVIX trade_date stale or mismatched: {actual_trade_date} != {expected_trade_date}")

    quality = str(row.get("quality", ""))
    avix = number(row, "avix_mid")
    if pd.notna(avix) and not (0 < float(avix) < 120):
        fail("realtime AVIX outside hard sanity range")

    v2_cols = {
        "near_dte", "next_dte", "near_n_options", "next_n_options",
        "near_n_puts", "next_n_puts", "near_n_calls", "next_n_calls",
        "months_fetched", "total_quotes", "valid_quotes", "median_spread_pct",
    }
    if quality == "OK" and v2_cols.issubset(df.columns):
        if not (float(number(row, "near_dte")) <= 30 <= float(number(row, "next_dte"))):
            fail("OK realtime AVIX does not bracket 30D")
        for col in ["near_n_options", "next_n_options"]:
            if float(number(row, col)) < 12:
                fail(f"OK realtime AVIX {col} too low")
        for col in ["near_n_puts", "next_n_puts", "near_n_calls", "next_n_calls"]:
            if float(number(row, col)) < 3:
                fail(f"OK realtime AVIX {col} too low")
        total_quotes = float(number(row, "total_quotes"))
        valid_quotes = float(number(row, "valid_quotes"))
        if float(number(row, "months_fetched")) < 2:
            fail("OK realtime AVIX needs at least two months")
        if valid_quotes / max(total_quotes, 1.0) < 0.45:
            fail("OK realtime AVIX valid quote ratio too low")
        spread = number(row, "median_spread_pct")
        if pd.notna(spread) and float(spread) > 0.30:
            fail("OK realtime AVIX median spread too wide")

    print(f"Realtime AVIX validation passed: quality={quality}, avix_mid={row.get('avix_mid')}")

if __name__ == "__main__":
    main()
