#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from src.storage.paths import CALCULATED, SITE

def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)

def main() -> None:
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
    avix = pd.read_csv(CALCULATED / "avix_clean_close.csv")
    if not avix.empty:
        require((avix["avix_clean"].dropna() > 0).all(), "avix_clean must be positive")

if __name__ == "__main__":
    main()
