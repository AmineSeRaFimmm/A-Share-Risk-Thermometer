#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main() -> None:
    latest = json.loads((ROOT / "data/site/latest.json").read_text(encoding="utf-8"))
    assert "risk_temperature" in latest
    assert 0 <= latest["risk_temperature"] <= 100
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    # Keep markers aligned with the redesigned three-view dashboard UI.
    for text in [
        "A-Share Risk Thermometer",
        'id="viewTemp"',
        "<h2>因子</h2>",
        'id="viewHistory"',
        "<h2>AVIX · QVIX</h2>",
        "<h2>沪深300 · 温度</h2>",
        'id="viewFlex"',
        'id="appDock"',
    ]:
        assert text in html, f"missing expected dashboard marker: {text!r}"

if __name__ == "__main__":
    main()
