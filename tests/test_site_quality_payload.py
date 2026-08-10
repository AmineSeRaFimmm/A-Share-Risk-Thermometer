from __future__ import annotations

import json

import pandas as pd

from src.core.site_data import components_payload, latest_payload


def _official_risk() -> pd.DataFrame:
    return pd.DataFrame([{
        "trade_date": "2026-07-29",
        "risk_temperature": 77.5,
        "regime": "PANIC",
        "regime_cn": "恐慌区",
        "quality": "OK",
        "avix_clean": 23.21,
        "avix_quality": "OK",
        "avix_percentile_2y": 86.9,
        "avix_zscore_1y": 87.4,
        "avix_5d_change": 73.9,
        "qvix_confirmation": 100.0,
        "qvix_close": 19.59,
        "qvix_source": "OPTBBS_PARSE_300ETF_QVIX_PROXY",
        "qvix_quote_time": "2026-07-29",
        "realized_vol_percentile": 94.4,
        "drawdown_pressure": 82.2,
        "market_breadth_pressure": 14.6,
        "turnover_stress": 8.4,
        "model_confidence": 100.0,
        "model_coverage_score": 100.0,
        "model_data_quality_score": 100.0,
        "model_missing_components": "",
    }])


def _estimate() -> dict:
    components = {
        "avix_percentile_2y": 89.9,
        "avix_zscore_1y": 98.9,
        "avix_5d_change": 100.0,
        "qvix_confirmation": 100.0,
        "realized_vol_percentile": 92.1,
        "drawdown_pressure": 88.6,
        "market_breadth_pressure": 84.2,
        "turnover_stress": 17.4,
    }
    return {
        "rows": [{
            "date": "2026-07-30",
            "trade_date": "2026-07-30",
            "risk_temperature_estimated": 89.8,
            "regime": "PANIC",
            "regime_cn": "恐慌区",
            "temperature_mode": "CLOSE_PENDING",
            "temperature_mode_cn": "收盘确认中",
            "quality": "OK_CLOSE_PENDING|WARN_QVIX_REALTIME_PROXY",
            "baseline_trade_date": "2026-07-29",
            "model_confidence": 84.9,
            "model_coverage_score": 100.0,
            "model_data_quality_score": 84.9,
            "model_missing_components": "QVIX_PROXY",
            "avix_realtime_mid": 24.56,
            "avix_realtime_source": "SINA_AKSHARE_REALTIME_MONTH_MID",
            "avix_realtime_quality_flags": "TIME_UNVERIFIED",
            "realtime_valuation_time": "2026-07-30T15:05:00+08:00",
            "qvix_close": 24.84,
            "qvix_source": "OPTBBS_CSV_300ETF_MIN_QVIX_PROXY",
            "qvix_quote_time": "2026-07-30T14:56:40+08:00",
            "realtime_index_source": "TENCENT_INDEX_QUOTE_RT",
            "realtime_index_quote_time": "2026-07-30T15:00:00+08:00",
            "hs300_close": 4549.72,
            "hs300_drawdown_60d": -0.101,
            "breadth_pressure": 84.2,
            "breadth_source": "PARSE_EM_A_SPOT",
            "breadth_secondary_source": "PARSE_EM_ZDFENBU",
            "breadth_source_score_delta": 0.5,
            "advancing_ratio": 0.315,
            "big_down_ratio": 0.189,
            "turnover_volume_ratio_20": 1.01,
            "components": components,
        }]
    }


def test_latest_and_components_preserve_state_and_quality_audit():
    risk = _official_risk()
    estimate = _estimate()
    latest = latest_payload(risk, pd.DataFrame(), pd.DataFrame(), estimate)
    components = components_payload(risk, pd.DataFrame(), estimate)

    assert latest["temperature_mode"] == "CLOSE_PENDING"
    assert latest["model_confidence"]["coverage_score"] == 100.0
    assert latest["model_confidence"]["data_quality_score"] == 84.9
    assert latest["market"]["advancing_ratio"] == 0.315
    assert latest["market"]["breadth_secondary_source"] == "PARSE_EM_ZDFENBU"
    assert latest["official_close"]["qvix_close"] == 19.59
    assert latest["official_close"]["qvix_source"] == "OPTBBS_PARSE_300ETF_QVIX_PROXY"
    assert latest["official_close"]["qvix_quote_time"] == "2026-07-29"

    qvix = next(item for item in components["components"] if item["key"] == "qvix_confirmation")
    assert qvix["raw_value"] == 24.84
    assert qvix["source"] == "OPTBBS_CSV_300ETF_MIN_QVIX_PROXY"
    assert qvix["quote_time"] == "2026-07-30T14:56:40+08:00"
    json.dumps({"latest": latest, "components": components}, allow_nan=False)
