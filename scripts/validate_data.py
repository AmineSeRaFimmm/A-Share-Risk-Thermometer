#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from pathlib import Path
import argparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from src.storage.paths import CALCULATED, SITE

def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-history", action="store_true")
    args = parser.parse_args()
    latest_path = SITE / "latest.json"
    risk_path = CALCULATED / "risk_temperature.csv"
    if not latest_path.exists() and (CALCULATED / "risk_components.csv").exists():
        from scripts.build_site_data import main as build
        build()
    require(latest_path.exists(), "latest.json missing")
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    require("quality" in latest, "latest quality missing")
    require(0 <= float(latest["risk_temperature"]) <= 100, "latest risk_temperature outside 0-100")
    rt = pd.read_csv(risk_path)
    require(not rt.empty, "risk_temperature.csv empty")
    require(rt["trade_date"].is_unique, "duplicate trade_date in risk_temperature.csv")
    require(rt["risk_temperature"].between(0, 100).all(), "risk_temperature outside 0-100")
    require(not rt["risk_temperature"].isna().any(), "risk_temperature contains null")
    for name in ["history.json", "components.json", "audit.json"]:
        path = SITE / name
        require(path.exists(), f"{name} missing")
        json.loads(path.read_text(encoding="utf-8"))
    nowcast_path = SITE / "nowcast_history.json"
    require(nowcast_path.exists(), "nowcast_history.json missing")
    nowcast = json.loads(nowcast_path.read_text(encoding="utf-8"))
    require("rows" in nowcast and "gaps" in nowcast, "nowcast_history missing rows/gaps")
    for row in nowcast.get("rows", []):
        require(0 <= float(row["risk_temperature_estimated"]) <= 100, "nowcast risk_temperature outside 0-100")
        require(row.get("temperature_mode") == "ESTIMATED_CLOSE", "nowcast row mode mismatch")
    sector_path = SITE / "sector_correlation.json"
    require(sector_path.exists(), "sector_correlation.json missing")
    sector = json.loads(sector_path.read_text(encoding="utf-8"))
    require(sector.get("sector_count", 0) >= 20, "sector_correlation sector_count too low")
    require(len(sector.get("metrics", [])) >= 100, "sector_correlation metrics too sparse")
    require(sector.get("rankings", {}).get("negative"), "sector_correlation negative ranking missing")
    low_position_path = SITE / "low_position_sector_study.json"
    require(low_position_path.exists(), "low_position_sector_study.json missing")
    low_position = json.loads(low_position_path.read_text(encoding="utf-8"))
    require(low_position.get("sector_count", 0) >= 20, "low_position sector_count too low")
    require(low_position.get("selected_count", 0) >= 4, "low_position selected_count too low")
    require(len(low_position.get("metrics", [])) >= 24, "low_position metrics too sparse")
    require(low_position.get("selected_sectors"), "low_position selected sectors missing")
    avix = pd.read_csv(CALCULATED / "avix_clean_close.csv")
    if not avix.empty:
        require((avix["avix_clean"].dropna() > 0).all(), "avix_clean must be positive")
        latest_avix_quality = str(avix.sort_values("trade_date").iloc[-1]["quality"])
        require("WARN_NOT_BRACKET_30D" not in latest_avix_quality, "latest AVIX must use a bracketed/exact 30D term")
    qvix = pd.read_csv(CALCULATED / "qvix_validation.csv")
    required_qvix = {
        "trade_date", "avix_clean", "qvix_close", "qvix_replica", "qvix_replica_quality",
        "qvix_replica_method", "avix_change_1d", "qvix_change_1d",
        "direction_match", "spread", "spread_zscore_252", "rolling_corr_60",
        "rolling_corr_120", "extreme_match", "qvix_confirmation", "quality",
    }
    require(required_qvix.issubset(qvix.columns), "qvix_validation.csv missing required validation columns")
    require(qvix["qvix_replica"].dropna().gt(0).all(), "qvix_replica must be positive when present")
    rates = pd.read_csv("data/normalized/rate_curve_history.csv")
    require(set([7, 14, 30, 90, 180, 365]).issubset(set(rates["tenor_days"].dropna().astype(int))), "Shibor curve missing required tenors")
    require(not rates["source"].astype(str).str.contains("FALLBACK|SYNTHETIC", case=False, regex=True).any(), "rate curve contains fallback/synthetic source")
    realtime_path = CALCULATED / "avix_realtime_mid.csv"
    require(realtime_path.exists(), "avix_realtime_mid.csv missing")
    realtime = pd.read_csv(realtime_path)
    require(not realtime.empty, "avix_realtime_mid.csv empty")
    require("avix_mid" in realtime.columns, "avix_realtime_mid.csv missing avix_mid")
    for csv_path in list(Path("data").glob("**/*.csv")):
        text = csv_path.read_text(encoding="utf-8", errors="ignore")
        require("SYNTHETIC_FALLBACK" not in text and "FALLBACK_CONSTANT_SHIBOR" not in text, f"{csv_path} contains synthetic/fallback production data")
    breadth = pd.read_csv("data/normalized/breadth_history.csv")
    if not breadth.empty and {"advancing_ratio", "decline_ratio", "big_down_ratio", "limit_down_ratio", "quality"}.issubset(breadth.columns):
        legacy = (
            breadth["quality"].astype(str).eq("WARN_BREADTH_MISSING")
            & pd.to_numeric(breadth["advancing_ratio"], errors="coerce").eq(0.5)
            & pd.to_numeric(breadth["decline_ratio"], errors="coerce").eq(0.5)
            & pd.to_numeric(breadth["big_down_ratio"], errors="coerce").eq(0)
            & pd.to_numeric(breadth["limit_down_ratio"], errors="coerce").eq(0)
        )
        require(not legacy.any(), "breadth_history contains legacy neutral fallback rows")
    if args.strict_history:
        index = pd.read_csv("data/normalized/index_history.csv")
        hs = index[index["symbol"] == "sh000300"]
        start_date = "2019-12-23"
        hs = hs[(hs["date"] >= start_date) & (hs["date"] <= avix["trade_date"].max())]
        require(hs["date"].min() <= "2020-01-02", "strict history requires index data back to 2020")
        require(avix["trade_date"].min() <= start_date, "strict history requires AVIX back to 2019-12-23")
        coverage = len(avix.dropna(subset=["avix_clean"])) / max(len(hs.drop_duplicates("date")), 1)
        require(coverage >= 0.75, f"strict history AVIX coverage below 75%: {coverage:.2%}")
        rt = pd.read_csv(CALCULATED / "risk_temperature.csv")
        for event_date, min_temp in {
            "2020-02-03": 70,
            "2022-03-15": 70,
            "2024-02-05": 70,
            "2025-04-07": 70,
        }.items():
            row = rt[rt["trade_date"] == event_date]
            require(not row.empty, f"strict history missing stress event date {event_date}")
            require(float(row.iloc[-1]["risk_temperature"]) >= min_temp, f"stress event {event_date} risk temperature too low")

if __name__ == "__main__":
    main()
