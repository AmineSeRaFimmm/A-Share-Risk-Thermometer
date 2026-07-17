#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.storage.paths import CALCULATED, NORMALIZED, RAW, ensure_dirs
from src.storage.csv_store import read_csv, write_csv
from src.data_sources.akshare_indices import fetch_index_daily
from src.data_sources.akshare_breadth import (
    fetch_a_breadth_snapshot,
    fetch_breadth_summary_multi,
    backfill_breadth_history_from_sohu,
    summarize_breadth,
)
from src.core.breadth import drop_legacy_synthetic_breadth
from scripts.bootstrap_history import (
    load_yaml,
    fetch_options,
    calculate_all,
    load_cached_option_frames,
    option_cache_max_date,
)
from src.core.contracts import build_contract_master
from scripts.build_site_data import main as build_site_data
import pandas as pd


def _ensure_breadth_for_date(trade_date: str) -> pd.DataFrame:
    """Resolve breadth via multi-source parse (EM spot / fenbu / Sohu ZDT)."""
    breadth_path = RAW / "breadth" / f"{trade_date}.csv"
    summary = pd.DataFrame()
    # Reuse cached full-board snapshot only when it still summarizes to OK.
    if breadth_path.exists():
        snap = read_csv(breadth_path)
        summary = summarize_breadth(snap, trade_date)
        if not summary.empty and str(summary.iloc[0].get("quality", "")).startswith("OK"):
            return summary
    try:
        summary = fetch_breadth_summary_multi(trade_date)
        # Persist stock-level snapshot when the winning path was EM spot.
        src = str(summary.iloc[0].get("source", "")) if not summary.empty else ""
        if "SPOT" in src or src.endswith("A_SPOT"):
            try:
                snap = fetch_a_breadth_snapshot()
                if not snap.empty and len(snap) >= 1000:
                    write_csv(snap, breadth_path)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        print(f"WARN breadth multi-source failed for {trade_date}: {exc}")
        if summary.empty:
            summary = summarize_breadth(pd.DataFrame(), trade_date)
    return summary


def _merge_breadth_history(summary: pd.DataFrame) -> pd.DataFrame:
    breadth_old = drop_legacy_synthetic_breadth(read_csv(NORMALIZED / "breadth_history.csv"))
    breadth = (
        pd.concat([breadth_old, summary], ignore_index=True).drop_duplicates("trade_date", keep="last")
        if not breadth_old.empty
        else summary
    )
    # Fill historical PROXY/MISSING holes from Sohu ZDT table when available.
    try:
        breadth = backfill_breadth_history_from_sohu(breadth)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN Sohu breadth backfill failed: {exc}")
    write_csv(breadth, NORMALIZED / "breadth_history.csv")
    return breadth


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
        df = (
            pd.concat([old, new], ignore_index=True).drop_duplicates(["date", "symbol"], keep="last")
            if not old.empty or not new.empty
            else old
        )
        write_csv(df, RAW / "indices" / f"{symbol}.csv")
        frames.append(df)
    index_history = pd.concat(frames, ignore_index=True)
    write_csv(index_history, NORMALIZED / "index_history.csv")
    hs = index_history[index_history["symbol"] == "sh000300"].copy()
    if hs.empty:
        raise RuntimeError("Cannot determine latest A-share trading day: HS300 index history is empty")
    trade_date = str(pd.to_datetime(hs["date"]).max().date())
    latest_clean = read_csv(CALCULATED / "avix_clean_close.csv")
    latest_done = None if latest_clean.empty else str(latest_clean["trade_date"].max())
    option_max = option_cache_max_date(limit=80)
    chain = read_csv(NORMALIZED / "daily_option_chain.csv")
    chain_max = None if chain.empty else str(pd.to_datetime(chain["trade_date"]).max().date())

    official_current = latest_done is not None and latest_done >= trade_date
    options_current = (option_max is not None and option_max >= trade_date) or (
        chain_max is not None and chain_max >= trade_date
    )
    if official_current and options_current:
        # Still refresh breadth for the day when possible, then rebuild site.
        summary = _ensure_breadth_for_date(trade_date)
        _merge_breadth_history(summary)
        build_site_data()
        return

    print(
        f"Daily update needed: trade_date={trade_date} "
        f"avix_clean_max={latest_done} option_cache_max={option_max} chain_max={chain_max}"
    )
    summary = _ensure_breadth_for_date(trade_date)
    _merge_breadth_history(summary)
    master, option_frames = fetch_options(index_history, recent_days=120)
    if not option_frames:
        option_frames = load_cached_option_frames()
        master = build_contract_master(option_frames)
        write_csv(master, NORMALIZED / "contract_master.csv")
    # If official series still lags, force a wider recompute tail so rolling windows stay correct.
    recompute_tail = 10
    if latest_done and latest_done < trade_date:
        recompute_tail = 20
    calculate_all(master, option_frames, index_history, recompute_tail_days=recompute_tail)
    build_site_data()

if __name__ == "__main__":
    main()
