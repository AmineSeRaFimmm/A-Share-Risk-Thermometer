from __future__ import annotations

import pandas as pd

from src.core.qvix_validation import validate_qvix
from src.core.risk_temperature import _model_confidence, _model_confidence_details


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


def test_qvix_validation_marks_legacy_daily_etf_source_as_proxy():
    avix = pd.DataFrame([{"trade_date": "2026-07-22", "avix_clean": 22.0}])
    qvix = pd.DataFrame([{
        "date": "2026-07-22",
        "close": 21.28,
        "source": "OPTBBS_PARSE_300ETF_QVIX",
    }])
    row = validate_qvix(avix, qvix).iloc[0]
    assert bool(row["is_proxy"])
    assert row["quality"] == "WARN_QVIX_REALTIME_PROXY"
    assert "PROXY" in row["quality_flags"]


def test_qvix_validation_preserves_delayed_index_metadata():
    avix = pd.DataFrame([{"trade_date": "2026-07-23", "avix_clean": 20.7}])
    qvix = pd.DataFrame(
        [{
            "date": "2026-07-23",
            "close": 20.2,
            "source": "EASTMONEY_CFFEX_300INDEX_QVIX_DELAYED",
            "qvix_quote_time": "2026-07-23T10:16:40+08:00",
            "qvix_delay_minutes": 15,
        }]
    )

    row = validate_qvix(avix, qvix).iloc[0]
    assert row["quality"] == "WARN_QVIX_DELAYED"
    assert row["qvix_quote_time"] == "2026-07-23T10:16:40+08:00"
    assert int(row["qvix_delay_minutes"]) == 15


def test_qvix_validation_preserves_cross_confirmation_metadata():
    avix = pd.DataFrame([{"trade_date": "2026-08-11", "avix_clean": 19.31}])
    qvix = pd.DataFrame([{
        "date": "2026-08-11",
        "close": 19.48,
        "source": "OPTBBS_300ETF_EOD_QVIX_PROXY_CROSSCHECKED_EASTMONEY_DELAYED",
        "qvix_quote_time": "2026-08-11T15:00:38+08:00",
        "is_final": True,
        "is_proxy": True,
        "is_delayed": True,
        "sample_size": 239,
        "observed": True,
        "quality_flags": "CROSS_CONFIRMED|DELAYED|EOD_PROVISIONAL|PROXY",
        "secondary_source": "EASTMONEY_CFFEX_300INDEX_QVIX_DELAYED",
        "secondary_close": 19.304886,
        "source_agreement": 0.991,
        "source_value_delta": 0.1751,
    }])

    row = validate_qvix(avix, qvix).iloc[0]

    assert row["secondary_source"] == "EASTMONEY_CFFEX_300INDEX_QVIX_DELAYED"
    assert float(row["secondary_close"]) == 19.304886
    assert float(row["source_agreement"]) == 0.991
    assert float(row["source_value_delta"]) == 0.1751
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


def test_model_confidence_discounts_delayed_index_replica_less_than_etf_proxy():
    row = pd.Series(
        {
            "avix_clean": 22.0,
            "avix_quality": "OK",
            "qvix_close": 20.1,
            "qvix_quality": "WARN_QVIX_DELAYED",
            "qvix_source": "EASTMONEY_CFFEX_300INDEX_QVIX_DELAYED",
            "realized_vol_percentile": 50.0,
            "drawdown_pressure": 50.0,
            "breadth_pressure": 35.0,
            "breadth_quality": "OK",
            "turnover_stress": 50.0,
        }
    )

    score, missing = _model_confidence(row)
    assert score == 97.6
    assert missing == "QVIX_DELAYED"


def test_neutral_fill_does_not_count_as_observed_data():
    row = pd.Series(
        {
            "avix_clean": 22.0,
            "avix_quality": "OK",
            "qvix_close": 21.28,
            "qvix_quality": "WARN_QVIX_REALTIME_PROXY",
            "qvix_source": "OPTBBS_CSV_300ETF_MIN_QVIX_PROXY",
            "realized_vol_percentile": 50.0,
            "realized_vol_observed": False,
            "drawdown_pressure": 50.0,
            "drawdown_observed": False,
            "breadth_pressure": 35.0,
            "breadth_quality": "OK",
            "turnover_stress": 50.0,
            "turnover_observed": False,
        }
    )

    details = _model_confidence_details(row)
    assert details["coverage_score"] == 72.0
    assert details["data_quality_score"] == 93.3
    assert details["score"] == 67.2
    assert details["missing_components"] == "QVIX_PROXY|REALIZED_VOL|DRAWDOWN|TURNOVER"
