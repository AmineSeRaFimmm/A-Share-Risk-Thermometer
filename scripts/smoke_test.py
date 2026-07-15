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
    # Keep markers aligned with the live dashboard (sector research panels were removed).
    for text in [
        "A股风险温度",
        "组件贡献",
        "AVIX",
        "QVIX",
        "正式收盘为实线",
        "Flex 执行台",
        "数据健康",
    ]:
        assert text in html, f"missing expected dashboard marker: {text!r}"

if __name__ == "__main__":
    main()
