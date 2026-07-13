#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import argparse
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.core.sector_correlation import analyze_sector_correlation
from src.data_sources.akshare_sectors import fetch_sw_level1_sector_history
from src.storage.csv_store import read_csv, write_csv
from src.storage.json_store import write_json
from src.storage.paths import CALCULATED, DOCS, NORMALIZED, RAW, SITE, ensure_dirs


def _merge_history(fresh: pd.DataFrame, cached: pd.DataFrame) -> pd.DataFrame:
    if fresh.empty and cached.empty:
        return pd.DataFrame()
    combined = pd.concat([cached, fresh], ignore_index=True) if not cached.empty else fresh.copy()
    combined["date"] = pd.to_datetime(combined["date"]).dt.strftime("%Y-%m-%d")
    combined = combined.drop_duplicates(["symbol", "date"], keep="last").sort_values(["symbol", "date"])
    return combined


def _cache_is_fresh(cached: pd.DataFrame, max_lag_days: int = 5) -> bool:
    if cached.empty or "date" not in cached.columns:
        return False
    try:
        max_date = pd.to_datetime(cached["date"], errors="coerce").max()
        if pd.isna(max_date):
            return False
        lag = (pd.Timestamp.now().normalize() - max_date.normalize()).days
        return lag <= max_lag_days
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch", action="store_true", help="Use cached sector data only")
    parser.add_argument("--force-fetch", action="store_true", help="Always refresh from network")
    args = parser.parse_args()

    ensure_dirs()
    normalized_path = NORMALIZED / "sw_level1_sector_history.csv"
    manifest_path = RAW / "sectors" / "sw_level1_fetch_manifest.csv"

    cached = read_csv(normalized_path)
    fresh = pd.DataFrame()
    manifest = pd.DataFrame()
    if args.no_fetch:
        print(f"Using cached SW L1 history only rows={len(cached)}")
    elif not args.force_fetch and _cache_is_fresh(cached):
        print(
            f"SW L1 cache is fresh (max_date={pd.to_datetime(cached['date']).max().date()}); "
            f"skipping network fetch rows={len(cached)}"
        )
    elif cached.empty or args.force_fetch:
        print("Fetching SW L1 sector history from network")
        try:
            fresh, manifest = fetch_sw_level1_sector_history()
        except Exception as exc:  # noqa: BLE001
            print(f"WARN sector fetch failed: {exc}")
            fresh, manifest = pd.DataFrame(), pd.DataFrame()
        if not manifest.empty:
            write_csv(manifest, manifest_path)
    else:
        # Cache exists but stale: attempt refresh, fall back to cache.
        print(f"SW L1 cache is stale; attempting refresh rows={len(cached)}")
        try:
            fresh, manifest = fetch_sw_level1_sector_history(max_workers=4)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN sector refresh failed, keeping cache: {exc}")
            fresh, manifest = pd.DataFrame(), pd.DataFrame()
        if not manifest.empty:
            write_csv(manifest, manifest_path)

    sectors = _merge_history(fresh, cached)
    if sectors.empty:
        raise SystemExit("sw_level1_sector_history.csv missing and fetch returned no data")
    write_csv(sectors, normalized_path)

    risk = read_csv(CALCULATED / "risk_components.csv")
    benchmark = read_csv(NORMALIZED / "index_history.csv")
    if risk.empty or benchmark.empty:
        raise SystemExit("risk_components.csv and index_history.csv are required")

    payload = analyze_sector_correlation(risk, sectors, benchmark)
    write_json(payload, SITE / "sector_correlation.json")
    write_csv(pd.DataFrame(payload["metrics"]), CALCULATED / "sector_correlation_metrics.csv")

    if DOCS.exists():
        data_dir = DOCS / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SITE / "sector_correlation.json", data_dir / "sector_correlation.json")
        downloads = data_dir / "downloads"
        downloads.mkdir(exist_ok=True)
        shutil.copy2(CALCULATED / "sector_correlation_metrics.csv", downloads / "sector_correlation_metrics.csv")

    print(
        "Sector correlation built: "
        f"{payload['sector_count']} sectors as_of={payload['as_of']} "
        f"positive={len(payload['rankings']['positive'])} negative={len(payload['rankings']['negative'])}"
    )


if __name__ == "__main__":
    main()
