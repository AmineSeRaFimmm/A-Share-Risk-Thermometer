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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch", action="store_true", help="Use cached sector data only")
    args = parser.parse_args()

    ensure_dirs()
    normalized_path = NORMALIZED / "sw_level1_sector_history.csv"
    manifest_path = RAW / "sectors" / "sw_level1_fetch_manifest.csv"

    cached = read_csv(normalized_path)
    fresh = pd.DataFrame()
    manifest = pd.DataFrame()
    if not args.no_fetch:
        fresh, manifest = fetch_sw_level1_sector_history()
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
