#!/usr/bin/env python3
from __future__ import annotations
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.storage.paths import WEB, DOCS, SITE, CALCULATED, ensure_dirs
from src.storage.csv_store import read_csv
from src.storage.json_store import write_json
from src.core.site_data import latest_payload, history_payload, components_payload, audit_payload
import pandas as pd

def main() -> None:
    ensure_dirs()
    risk = read_csv(CALCULATED / "risk_components.csv")
    raw = read_csv(CALCULATED / "avix_raw_close.csv")
    realtime = read_csv(CALCULATED / "avix_realtime_mid.csv")
    if risk.empty:
        raise SystemExit("risk_components.csv is missing or empty")
    write_json(latest_payload(risk, raw, realtime), SITE / "latest.json")
    write_json(history_payload(risk), SITE / "history.json")
    write_json(components_payload(risk), SITE / "components.json")
    write_json(audit_payload(risk, realtime), SITE / "audit.json")
    write_json({
        "title": "A-Share Risk Thermometer methodology",
        "not_official": True,
        "avix_history": "AVIX_CLOSE_REPLICA uses historical close prices.",
        "avix_realtime": "AVIX_REALTIME_MID uses bid/ask midpoint when available.",
        "qvix_role": "QVIX is validation and confirmation only.",
        "weights": "Weights are configured in config/scoring.yml.",
        "limitations": ["No historical bid/ask", "No historical settlement price", "No historical open interest", "Free sources may fail or change"],
    }, SITE / "methodology.json")
    if DOCS.exists():
        shutil.rmtree(DOCS)
    shutil.copytree(WEB, DOCS)
    data_dir = DOCS / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for path in SITE.glob("*.json"):
        shutil.copy2(path, data_dir / path.name)
    downloads = data_dir / "downloads"
    downloads.mkdir(exist_ok=True)
    for name in ["risk_temperature.csv", "avix_clean_close.csv", "qvix_validation.csv"]:
        src = CALCULATED / name
        if src.exists():
            shutil.copy2(src, downloads / name)
    write_json({"build_time": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")}, data_dir / "build_info.json")

if __name__ == "__main__":
    main()
