#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.core.avix_formula import calculate_avix_for_date
from src.core.breadth import drop_legacy_synthetic_breadth
from src.core.clean_surface import clean_option_surface
from src.data_sources.akshare_breadth import fetch_a_breadth_snapshot, summarize_breadth
from scripts.bootstrap_history import refresh_qvix_and_risk_outputs
from src.storage.csv_store import read_csv, write_csv
from src.storage.paths import CALCULATED, NORMALIZED, RAW, ensure_dirs


def calculate_clean(chain: pd.DataFrame, rates: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    clean_chain = clean_option_surface(chain, rates)
    clean_for_calc = clean_chain.copy()
    if "clean_valid" in clean_for_calc.columns:
        clean_for_calc["valid_price"] = clean_for_calc["clean_valid"].astype(bool)
    dates = sorted(clean_for_calc["trade_date"].dropna().astype(str).unique().tolist())
    by_date = {str(d): g.copy() for d, g in clean_for_calc.groupby("trade_date")}
    rows = [calculate_avix_for_date(by_date[d], rates, d, "clean_price") for d in dates]
    clean = pd.DataFrame(rows)
    if clean.empty:
        return clean
    clean = clean.rename(columns={"avix": "avix_clean"}).merge(raw[["trade_date", "avix_raw"]], on="trade_date", how="left")
    clean["raw_clean_diff"] = (clean["avix_clean"] - clean["avix_raw"]).abs()
    counts = clean_chain.groupby("trade_date")["clean_valid"].sum()
    totals = clean_chain.groupby("trade_date").size()
    clean["cleaned_option_count"] = counts.reindex(clean["trade_date"]).values
    clean["cleaned_option_ratio"] = clean["cleaned_option_count"] / totals.reindex(clean["trade_date"]).values
    clean["clean_method"] = "iv_filter_cp_rolling_median_v2"
    clean.loc[clean["raw_clean_diff"] > 2.0, "quality"] = clean["quality"].astype(str) + "|WARN_CLEAN_IMPACT_HIGH"
    clean.loc[clean["raw_clean_diff"] > 4.0, "quality"] = clean["quality"].astype(str) + "|LOW_CLEAN_IMPACT_TOO_HIGH"
    from scripts.bootstrap_history import _trim_unusable_official_avix_tip

    return _trim_unusable_official_avix_tip(clean)


def main() -> None:
    ensure_dirs()
    chain = read_csv(NORMALIZED / "daily_option_chain.csv")
    rates = read_csv(NORMALIZED / "rate_curve_history.csv")
    raw = read_csv(CALCULATED / "avix_raw_close.csv")
    index_history = read_csv(NORMALIZED / "index_history.csv")
    if chain.empty:
        raise SystemExit("daily_option_chain.csv missing or empty")
    if rates.empty:
        raise SystemExit("rate_curve_history.csv missing or empty")
    if raw.empty:
        raise SystemExit("avix_raw_close.csv missing or empty")
    clean = calculate_clean(chain, rates, raw)
    write_csv(clean, CALCULATED / "avix_clean_close.csv")
    hs = index_history[index_history["symbol"] == "sh000300"].copy()
    latest_trade_date = str(pd.to_datetime(hs["date"]).max().date()) if not hs.empty else str(clean["trade_date"].max())
    breadth_hist = drop_legacy_synthetic_breadth(read_csv(NORMALIZED / "breadth_history.csv"))
    if breadth_hist.empty or latest_trade_date not in set(breadth_hist.get("trade_date", [])):
        try:
            breadth_raw = fetch_a_breadth_snapshot()
        except Exception as exc:  # noqa: BLE001
            print(f"WARN breadth fetch failed: {exc}")
            breadth_raw = pd.DataFrame()
        if not breadth_raw.empty:
            write_csv(breadth_raw, RAW / "breadth" / f"{latest_trade_date}.csv")
        summary = summarize_breadth(breadth_raw, latest_trade_date)
        breadth_hist = pd.concat([breadth_hist, summary], ignore_index=True).drop_duplicates("trade_date", keep="last")
        write_csv(breadth_hist, NORMALIZED / "breadth_history.csv")
    refresh_qvix_and_risk_outputs(
        clean,
        index_history,
        rates,
        event="recalculate_from_cache",
    )


if __name__ == "__main__":
    main()
