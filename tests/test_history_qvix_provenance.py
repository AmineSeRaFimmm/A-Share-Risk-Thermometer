from __future__ import annotations

import pandas as pd
from pathlib import Path

from src.core.site_data import history_payload


ROOT = Path(__file__).resolve().parents[1]


def test_history_payload_preserves_qvix_provenance() -> None:
    risk = pd.DataFrame([
        {
            "trade_date": "2026-08-06",
            "risk_temperature": 60.0,
            "regime": "HIGH_RISK",
            "qvix_close": 20.3,
            "qvix_source": "OPTBBS_PARSE_300INDEX_QVIX",
            "qvix_quality": "OK",
            "is_proxy": False,
        },
        {
            "trade_date": "2026-08-07",
            "risk_temperature": 59.5,
            "regime": "CAUTION",
            "qvix_close": 19.5,
            "qvix_source": "OPTBBS_PARSE_300ETF_QVIX_PROXY",
            "qvix_quality": "WARN_QVIX_REALTIME_PROXY",
            "is_proxy": True,
        },
    ])

    rows = history_payload(risk)

    assert rows[0]["qvix_is_proxy"] is False
    assert rows[0]["qvix_source"] == "OPTBBS_PARSE_300INDEX_QVIX"
    assert rows[1]["qvix_is_proxy"] is True
    assert rows[1]["qvix_quality"] == "WARN_QVIX_REALTIME_PROXY"


def test_history_chart_separates_real_and_proxy_qvix() -> None:
    charts = (ROOT / "web/assets/charts.js").read_text(encoding="utf-8")
    assert "qvixProxy: '300ETF QVIX代理'" in charts
    assert "d.qvix_is_proxy ? null : positiveOrNull(d.qvix)" in charts
    assert "d.qvix_is_proxy ? positiveOrNull(d.qvix) : null" in charts
