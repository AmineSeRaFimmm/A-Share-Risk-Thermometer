#!/usr/bin/env python3
from __future__ import annotations
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.storage.paths import WEB, DOCS, SITE, CALCULATED, NORMALIZED, ensure_dirs
from src.storage.csv_store import read_csv
from src.storage.json_store import write_json
from src.core.site_data import latest_payload, history_payload, components_payload, audit_payload, strategy_payload
from src.core.strategy_s3_s4 import build_s3_s4_strategy
from src.core.sector_correlation import analyze_sector_correlation
from src.core.low_position_sector_study import analyze_low_position_sector_study
from src.core.nowcast_history import build_nowcast_history_from_files, nowcast_rows_csv
import pandas as pd

def main() -> None:
    ensure_dirs()
    risk = read_csv(CALCULATED / "risk_components.csv")
    raw = read_csv(CALCULATED / "avix_raw_close.csv")
    realtime = read_csv(CALCULATED / "avix_realtime_mid.csv")
    avix_clean = read_csv(CALCULATED / "avix_clean_close.csv")
    index_history = read_csv(NORMALIZED / "index_history.csv")
    if risk.empty:
        raise SystemExit("risk_components.csv is missing or empty")
    strategy = build_s3_s4_strategy(avix_clean, index_history)
    if not strategy.empty:
        strategy.to_csv(CALCULATED / "strategy_s3_s4.csv", index=False)
    write_json(history_payload(risk), SITE / "history.json")
    nowcast_history = build_nowcast_history_from_files()
    write_json(nowcast_history, SITE / "nowcast_history.json")
    nowcast_csv = nowcast_rows_csv(nowcast_history)
    nowcast_csv.to_csv(CALCULATED / "risk_temperature_nowcast.csv", index=False)
    write_json(latest_payload(risk, raw, realtime, nowcast_history), SITE / "latest.json")
    write_json(components_payload(risk, realtime, nowcast_history), SITE / "components.json")
    write_json(audit_payload(risk, realtime, nowcast_history), SITE / "audit.json")
    write_json(strategy_payload(strategy), SITE / "strategy.json")
    sector_history = read_csv(NORMALIZED / "sw_level1_sector_history.csv")
    if not sector_history.empty and not index_history.empty:
        sector_payload = analyze_sector_correlation(risk, sector_history, index_history)
        write_json(sector_payload, SITE / "sector_correlation.json")
        pd.DataFrame(sector_payload["metrics"]).to_csv(CALCULATED / "sector_correlation_metrics.csv", index=False)
        valuation = read_csv(Path("data/raw/sectors/sw_level1_valuation_snapshot.csv"))
        low_position_payload = analyze_low_position_sector_study(risk, sector_history, index_history, valuation=valuation)
        write_json(low_position_payload, SITE / "low_position_sector_study.json")
        pd.DataFrame(low_position_payload["metrics"]).to_csv(CALCULATED / "low_position_sector_metrics.csv", index=False)
    write_json({
        "title": "A-Share Risk Thermometer methodology",
        "not_official": True,
        "avix_history": "AVIX_CLOSE_REPLICA uses historical close prices.",
        "avix_realtime": "AVIX_REALTIME_MID uses bid/ask midpoint when available.",
        "qvix_role": "QVIX is validation and confirmation only.",
        "weights": "Weights are configured in config/scoring.yml.",
        "model_confidence": "model_confidence scores input completeness separately from risk_temperature; proxy breadth is discounted and flagged.",
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
    for name in ["risk_temperature.csv", "risk_temperature_nowcast.csv", "avix_clean_close.csv", "qvix_validation.csv", "strategy_s3_s4.csv", "sector_correlation_metrics.csv", "low_position_sector_metrics.csv"]:
        src = CALCULATED / name
        if src.exists():
            shutil.copy2(src, downloads / name)
    write_json({"build_time": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")}, data_dir / "build_info.json")

if __name__ == "__main__":
    main()
