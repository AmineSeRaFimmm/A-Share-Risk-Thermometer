#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.storage.paths import CALCULATED, SITE, DOCS
from src.storage.csv_store import read_csv
from src.storage.json_store import read_json
from src.storage.json_store import write_json
from src.core.site_data import components_payload


def main() -> None:
    risk = read_csv(CALCULATED / "risk_components.csv")
    realtime = read_csv(CALCULATED / "avix_realtime_mid.csv")
    nowcast_history = read_json(SITE / "nowcast_history.json", default={"rows": [], "gaps": []})
    if risk.empty:
        raise SystemExit("risk_components.csv is missing or empty")
    payload = components_payload(risk, realtime, nowcast_history)
    write_json(payload, SITE / "components.json")
    docs_data = DOCS / "data"
    if docs_data.exists():
        write_json(payload, docs_data / "components.json")
    print(f"Active components written: {payload.get('temperature_mode')} {payload.get('trade_date')}")


if __name__ == "__main__":
    main()
