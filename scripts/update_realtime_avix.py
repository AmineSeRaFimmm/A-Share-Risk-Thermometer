#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.storage.paths import RAW, NORMALIZED, CALCULATED, ensure_dirs
from src.storage.csv_store import read_csv, write_csv
from src.data_sources.akshare_options import fetch_option_realtime_months
from src.core.calendar import trading_days_from_index
from src.core.realtime_avix import calculate_realtime_avix


def latest_trade_date(index_history: pd.DataFrame) -> str:
    hs = index_history[index_history["symbol"] == "sh000300"].copy()
    return str(hs.sort_values("date").iloc[-1]["date"])


def latest_clean_avix(trade_date: str) -> float | None:
    df = read_csv(CALCULATED / "avix_clean_close.csv")
    if df.empty or "avix_clean" not in df.columns:
        return None
    df = df[df["trade_date"].astype(str) <= str(trade_date)].copy()
    df["avix_clean"] = pd.to_numeric(df["avix_clean"], errors="coerce")
    df = df.dropna(subset=["avix_clean"]).sort_values("trade_date")
    return None if df.empty else float(df.iloc[-1]["avix_clean"])


def main() -> None:
    ensure_dirs()
    index_history = read_csv(NORMALIZED / "index_history.csv")
    rates = read_csv(NORMALIZED / "rate_curve_history.csv")
    if index_history.empty or rates.empty:
        raise SystemExit("index_history.csv and rate_curve_history.csv are required")

    trade_date = latest_trade_date(index_history)
    raw, manifest = fetch_option_realtime_months()
    if not raw.empty:
        write_csv(raw, RAW / "option_realtime" / f"{trade_date}.csv")
    if not manifest.empty:
        write_csv(manifest, RAW / "option_realtime" / "fetch_manifest.csv")

    hs = index_history[index_history["symbol"] == "sh000300"]
    chain, result = calculate_realtime_avix(
        raw,
        rates,
        trade_date,
        set(trading_days_from_index(hs)),
        close_avix=latest_clean_avix(trade_date),
        fetch_manifest=manifest,
    )
    if not chain.empty:
        write_csv(chain, NORMALIZED / "realtime_option_chain.csv")
    write_csv(result, CALCULATED / "avix_realtime_mid.csv")

    if (CALCULATED / "risk_components.csv").exists():
        from scripts.build_site_data import main as build_site
        from scripts.write_active_components import main as write_active_components
        build_site()
        write_active_components()

    row = result.iloc[-1].to_dict()
    print(f"Realtime AVIX updated: {trade_date} quality={row.get('quality')} avix_mid={row.get('avix_mid')}")


if __name__ == "__main__":
    main()
