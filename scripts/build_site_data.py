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
from src.core.rt_tactical import build_rt_tactical_payload
from src.core.stage_trade_playbook import build_playbook_payload
import pandas as pd


def _sync_web_to_docs() -> None:
    """Copy web/ into docs/ without wiping unrelated data/ artifacts first."""
    DOCS.mkdir(parents=True, exist_ok=True)
    for path in WEB.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(WEB)
        dest = DOCS / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def _payload_is_fresh(path: Path, risk: pd.DataFrame, max_age_hours: float = 36.0) -> bool:
    """True when JSON exists, is recent, and matches the latest risk trade_date when possible."""
    if not path.exists() or risk.empty:
        return False
    try:
        import json
        import time

        age_hours = (time.time() - path.stat().st_mtime) / 3600.0
        if age_hours > max_age_hours:
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        as_of = str(payload.get("as_of") or payload.get("benchmark_latest_date") or "")
        latest_risk = str(risk.sort_values("trade_date").iloc[-1]["trade_date"])
        if as_of and as_of < latest_risk:
            return False
        return True
    except Exception:
        return False


def _load_or_build_sector_payloads(risk: pd.DataFrame, index_history: pd.DataFrame) -> None:
    """Reuse sector JSON when fresh; otherwise recompute from local sector history."""
    sector_path = SITE / "sector_correlation.json"
    low_path = SITE / "low_position_sector_study.json"
    sector_history = read_csv(NORMALIZED / "sw_level1_sector_history.csv")

    if sector_history.empty or index_history.empty:
        return

    if not _payload_is_fresh(sector_path, risk):
        sector_payload = analyze_sector_correlation(risk, sector_history, index_history)
        write_json(sector_payload, sector_path)
        pd.DataFrame(sector_payload["metrics"]).to_csv(CALCULATED / "sector_correlation_metrics.csv", index=False)

    if not _payload_is_fresh(low_path, risk):
        valuation = read_csv(Path("data/raw/sectors/sw_level1_valuation_snapshot.csv"))
        low_position_payload = analyze_low_position_sector_study(
            risk, sector_history, index_history, valuation=valuation
        )
        write_json(low_position_payload, low_path)
        pd.DataFrame(low_position_payload["metrics"]).to_csv(
            CALCULATED / "low_position_sector_metrics.csv", index=False
        )


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
    write_json(build_rt_tactical_payload(risk, index_history), SITE / "rt_tactical.json")
    write_json(build_playbook_payload(risk, index_history), SITE / "stage_playbook.json")
    _load_or_build_sector_payloads(risk, index_history)
    write_json({
        "title": "A-Share Risk Thermometer methodology",
        "not_official": True,
        "avix_history": "AVIX_CLOSE_REPLICA uses historical close prices.",
        "avix_realtime": "AVIX_REALTIME_MID uses bid/ask midpoint when available.",
        "qvix_role": "QVIX is validation and confirmation only (agreement quality, not fear level).",
        "weights": "Weights are loaded at runtime from config/scoring.yml.",
        "model_confidence": "model_confidence scores input completeness separately from risk_temperature; proxy breadth is discounted and flagged.",
        "limitations": [
            "No historical bid/ask",
            "No historical settlement price",
            "No historical open interest",
            "Free sources may fail or change",
            "Historical breadth is often index-proxy when stock breadth snapshots are missing",
            "Missing component scores fill with neutral 50 (not reweighted); see model_confidence",
            "Percentile windows use min_history_days_for_percentile from config/thresholds.yml",
        ],
    }, SITE / "methodology.json")
    _sync_web_to_docs()
    data_dir = DOCS / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for path in SITE.glob("*.json"):
        shutil.copy2(path, data_dir / path.name)
    downloads = data_dir / "downloads"
    downloads.mkdir(exist_ok=True)
    for name in [
        "risk_temperature.csv",
        "risk_temperature_nowcast.csv",
        "avix_clean_close.csv",
        "qvix_validation.csv",
        "strategy_s3_s4.csv",
        "sector_correlation_metrics.csv",
        "low_position_sector_metrics.csv",
    ]:
        src = CALCULATED / name
        if src.exists():
            shutil.copy2(src, downloads / name)
    write_json(
        {"build_time": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")},
        data_dir / "build_info.json",
    )

if __name__ == "__main__":
    main()
