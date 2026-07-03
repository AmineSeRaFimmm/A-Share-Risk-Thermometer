#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.storage.paths import NORMALIZED, RAW, ensure_dirs
from src.storage.csv_store import read_csv, write_csv
from src.data_sources.akshare_indices import fetch_index_daily
from src.data_sources.akshare_breadth import fetch_a_breadth_snapshot, summarize_breadth
from src.utils.dates import today_cn
from scripts.bootstrap_history import load_yaml, fetch_options, calculate_all
import pandas as pd

def main() -> None:
    ensure_dirs()
    universe = load_yaml("universe.yml")
    frames = []
    for cfg in universe["indices"].values():
        symbol = cfg["symbol"]
        old = read_csv(RAW / "indices" / f"{symbol}.csv")
        try:
            new = fetch_index_daily(symbol)
        except Exception:
            new = pd.DataFrame()
        df = pd.concat([old, new], ignore_index=True).drop_duplicates(["date", "symbol"], keep="last") if not old.empty or not new.empty else old
        write_csv(df, RAW / "indices" / f"{symbol}.csv")
        frames.append(df)
    index_history = pd.concat(frames, ignore_index=True)
    write_csv(index_history, NORMALIZED / "index_history.csv")
    trade_date = str(today_cn())
    try:
        snap = fetch_a_breadth_snapshot()
        if not snap.empty:
            write_csv(snap, RAW / "breadth" / f"{trade_date}.csv")
        summary = summarize_breadth(snap, trade_date)
    except Exception:
        summary = summarize_breadth(pd.DataFrame(), trade_date)
    breadth_old = read_csv(NORMALIZED / "breadth_history.csv")
    breadth = pd.concat([breadth_old, summary], ignore_index=True).drop_duplicates("trade_date", keep="last") if not breadth_old.empty else summary
    write_csv(breadth, NORMALIZED / "breadth_history.csv")
    master, frames = fetch_options(index_history, recent_days=120)
    calculate_all(master, frames, index_history)

if __name__ == "__main__":
    main()
