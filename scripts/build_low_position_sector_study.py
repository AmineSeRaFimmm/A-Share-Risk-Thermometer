#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.core.low_position_sector_study import analyze_low_position_sector_study
from src.data_sources.akshare_sectors import fetch_sw_level1_sector_valuation
from src.storage.csv_store import read_csv, write_csv
from src.storage.json_store import write_json
from src.storage.paths import CALCULATED, DOCS, NORMALIZED, RAW, SITE, ensure_dirs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch", action="store_true", help="Use cached valuation data only")
    parser.add_argument("--selected-count", type=int, default=5)
    args = parser.parse_args()

    ensure_dirs()
    valuation_path = RAW / "sectors" / "sw_level1_valuation_snapshot.csv"

    valuation = read_csv(valuation_path)
    if not args.no_fetch:
        fresh = fetch_sw_level1_sector_valuation()
        if not fresh.empty:
            valuation = fresh
            write_csv(valuation, valuation_path)

    risk = read_csv(CALCULATED / "risk_components.csv")
    sectors = read_csv(NORMALIZED / "sw_level1_sector_history.csv")
    benchmark = read_csv(NORMALIZED / "index_history.csv")
    if risk.empty or sectors.empty or benchmark.empty:
        raise SystemExit("risk_components.csv, sw_level1_sector_history.csv and index_history.csv are required")

    payload = analyze_low_position_sector_study(
        risk,
        sectors,
        benchmark,
        valuation=valuation,
        selected_count=args.selected_count,
    )
    write_json(payload, SITE / "low_position_sector_study.json")
    write_csv(pd.DataFrame(payload["metrics"]), CALCULATED / "low_position_sector_metrics.csv")

    if DOCS.exists():
        data_dir = DOCS / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SITE / "low_position_sector_study.json", data_dir / "low_position_sector_study.json")
        downloads = data_dir / "downloads"
        downloads.mkdir(exist_ok=True)
        shutil.copy2(CALCULATED / "low_position_sector_metrics.csv", downloads / "low_position_sector_metrics.csv")

    names = ", ".join(row["name"] for row in payload["selected_sectors"])
    print(
        "Low-position sector study built: "
        f"{payload['selected_count']} selected as_of={payload['as_of']} [{names}]"
    )


if __name__ == "__main__":
    main()
