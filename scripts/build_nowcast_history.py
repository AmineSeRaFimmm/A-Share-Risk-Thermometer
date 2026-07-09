#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.nowcast_history import build_nowcast_history_from_files, nowcast_rows_csv
from src.storage.csv_store import write_csv
from src.storage.json_store import write_json
from src.storage.paths import CALCULATED, DOCS, SITE, ensure_dirs


def main() -> None:
    ensure_dirs()
    payload = build_nowcast_history_from_files()
    write_json(payload, SITE / "nowcast_history.json")
    write_csv(nowcast_rows_csv(payload), CALCULATED / "risk_temperature_nowcast.csv")
    if DOCS.exists():
        data_dir = DOCS / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SITE / "nowcast_history.json", data_dir / "nowcast_history.json")
        downloads = data_dir / "downloads"
        downloads.mkdir(exist_ok=True)
        shutil.copy2(CALCULATED / "risk_temperature_nowcast.csv", downloads / "risk_temperature_nowcast.csv")
    rows = payload.get("rows", [])
    print(
        "Nowcast history built: "
        f"status={payload.get('status')} rows={len(rows)} "
        f"official_latest={payload.get('official_latest_date')} "
        f"estimated_latest={payload.get('estimated_latest_date')}"
    )


if __name__ == "__main__":
    main()
