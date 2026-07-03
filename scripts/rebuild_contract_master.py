#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from src.storage.paths import RAW, NORMALIZED, ensure_dirs
from src.storage.csv_store import read_csv, write_csv
from src.core.contracts import build_contract_master
from src.core.option_chain import build_daily_option_chain
from src.core.calendar import trading_days_from_index

def main() -> None:
    ensure_dirs()
    frames = [read_csv(path) for path in sorted((RAW / "options_daily").glob("*.csv"))]
    frames = [df for df in frames if not df.empty]
    master = build_contract_master(frames)
    write_csv(master, NORMALIZED / "contract_master.csv")
    index_history = read_csv(NORMALIZED / "index_history.csv")
    trading_days = set(trading_days_from_index(index_history[index_history["symbol"] == "sh000300"])) if not index_history.empty else set()
    chain = build_daily_option_chain(master, frames, trading_days) if trading_days else pd.DataFrame()
    write_csv(chain, NORMALIZED / "daily_option_chain.csv")
    print(f"contracts={len(master)} chain_rows={len(chain)}")

if __name__ == "__main__":
    main()
