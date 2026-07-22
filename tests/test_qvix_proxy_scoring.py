from __future__ import annotations

import pandas as pd

from src.core.qvix_validation import validate_qvix
from src.core.risk_temperature import _model_confidence


def test_qvix_validation_marks_realtime_etf_proxy_quality():
    avix = pd.DataFrame(
        [
            {"trade_date": "2026-07-21", "avix_clean": 23.0},
            {"trade_date": "2026-07-22", "avix_clean": 22.0},
        ]
    )
    qvix = pd.DataFrame(
        [
            {
                "date": "2026-07-22",
                "close": 21.28,
                "source": "OPTBBS_CSV_300ETF_MIN_QVIX_PROXY",
            }
        ]
    )

    out = validate_qvix(avix, qvix)
    row = out[out["trade_date"] == "2026-07-22"].iloc[0]
    assert row["qvix_source"] == "OPTBBS_CSV_300ETF_MIN_QVIX_PROXY"
    assert row["quality"] == "WARN_QVIX_REALTIME_PROXY"


def test_model_confidence_discounts_qvix_proxy_weight():
    row = pd.Series(
        {
            "avix_clean": 22.0,
            "avix_quality": "OK",
            "qvix_close": 21.28,
            "qvix_quality": "WARN_QVIX_REALTIME_PROXY",
            "qvix_source": "OPTBBS_CSV_300ETF_MIN_QVIX_PROXY",
            "realized_vol_percentile": 50.0,
            "drawdown_pressure": 50.0,
            "breadth_pressure": 35.0,
            "breadth_quality": "OK",
            "turnover_stress": 50.0,
        }
    )

    score, missing = _model_confidence(row)
    assert score == 95.2
    assert missing == "QVIX_PROXY"
