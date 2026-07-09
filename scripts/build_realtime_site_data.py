#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.nowcast_history import build_nowcast_history_from_files, nowcast_rows_csv
from src.core.site_data import audit_payload, components_payload, latest_payload
from src.storage.csv_store import read_csv, write_csv
from src.storage.json_store import write_json
from src.storage.paths import CALCULATED, DOCS, SITE, ensure_dirs


def main() -> None:
    ensure_dirs()
    risk = read_csv(CALCULATED / "risk_components.csv")
    raw = read_csv(CALCULATED / "avix_raw_close.csv")
    realtime = read_csv(CALCULATED / "avix_realtime_mid.csv")
    if risk.empty:
        raise SystemExit("risk_components.csv is missing or empty")

    nowcast_history = build_nowcast_history_from_files()
    write_json(nowcast_history, SITE / "nowcast_history.json")
    write_csv(nowcast_rows_csv(nowcast_history), CALCULATED / "risk_temperature_nowcast.csv")
    write_json(latest_payload(risk, raw, realtime, nowcast_history), SITE / "latest.json")
    write_json(components_payload(risk, realtime, nowcast_history), SITE / "components.json")
    write_json(audit_payload(risk, realtime, nowcast_history), SITE / "audit.json")

    data_dir = DOCS / "data"
    if data_dir.exists():
        for name in ["latest.json", "components.json", "audit.json", "nowcast_history.json"]:
            shutil.copy2(SITE / name, data_dir / name)
        downloads = data_dir / "downloads"
        downloads.mkdir(exist_ok=True)
        shutil.copy2(CALCULATED / "risk_temperature_nowcast.csv", downloads / "risk_temperature_nowcast.csv")

    print(
        "Realtime site data built: "
        f"latest={nowcast_history.get('estimated_latest_date') or risk['trade_date'].max()} "
        f"nowcast_rows={len(nowcast_history.get('rows', []))}"
    )


if __name__ == "__main__":
    main()
