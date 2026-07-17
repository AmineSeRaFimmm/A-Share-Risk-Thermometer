#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.core.avix_formula import calculate_avix_for_date
from src.core.breadth import compute_breadth_pressure, drop_legacy_synthetic_breadth
from src.core.clean_surface import clean_option_surface
from src.core.drawdown import compute_drawdown
from src.core.qvix_validation import validate_qvix
from src.core.realized_vol import compute_realized_vol
from src.core.risk_temperature import compute_risk_temperature
from src.data_sources.akshare_breadth import fetch_a_breadth_snapshot, summarize_breadth
from src.data_sources.akshare_qvix import fetch_qvix
from src.storage.csv_store import read_csv, write_csv
from src.storage.paths import CALCULATED, NORMALIZED, RAW, ensure_dirs
from src.utils.dates import now_cn


def calculate_clean(chain: pd.DataFrame, rates: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    if len(chain) > 100_000:
        # Same official-tip policy as bootstrap_history.calculate_all:
        # WARN_NOT_BRACKET_30D is kept; only unusable AVIX tips are dropped.
        from scripts.bootstrap_history import _trim_unusable_official_avix_tip

        clean = raw.rename(columns={"avix_raw": "avix_clean"}).copy()
        clean = _trim_unusable_official_avix_tip(clean)
        clean["avix_raw"] = clean["avix_clean"]
        clean["raw_clean_diff"] = 0.0
        clean["cleaned_option_count"] = clean[["near_n_options", "next_n_options"]].min(axis=1)
        clean["cleaned_option_ratio"] = 1.0
        clean["clean_method"] = "moneyness_filter_fast_from_raw"
        return clean
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
    clean["clean_method"] = "moneyness_filter_fast" if len(chain) > 100_000 else "iv_filter_rolling_median"
    clean.loc[clean["raw_clean_diff"] > 2.0, "quality"] = clean["quality"].astype(str) + "|WARN_CLEAN_IMPACT_HIGH"
    clean.loc[clean["raw_clean_diff"] > 4.0, "quality"] = clean["quality"].astype(str) + "|LOW_CLEAN_IMPACT_TOO_HIGH"
    return clean


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
    try:
        qvix = fetch_qvix()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN qvix fetch failed: {exc}")
        qvix = read_csv(RAW / "qvix" / "qvix.csv")
    write_csv(qvix, RAW / "qvix" / "qvix.csv")
    qv = validate_qvix(clean, qvix)
    write_csv(qv, CALCULATED / "qvix_validation.csv")
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
    breadth = compute_breadth_pressure(breadth_hist)
    realized = compute_realized_vol(index_history)
    drawdown = compute_drawdown(index_history)
    components = compute_risk_temperature(clean, qv, realized, drawdown, breadth, index_history)
    write_csv(components, CALCULATED / "risk_components.csv")
    write_csv(components[["trade_date", "risk_temperature", "regime", "regime_cn", "quality"]], CALCULATED / "risk_temperature.csv")
    audit = pd.DataFrame([{
        "trade_date": components["trade_date"].iloc[-1],
        "event": "recalculate_from_cache",
        "quality": components["quality"].iloc[-1],
        "time": now_cn().isoformat(timespec="seconds"),
    }]) if not components.empty else pd.DataFrame()
    write_csv(audit, CALCULATED / "audit_log.csv")


if __name__ == "__main__":
    main()
