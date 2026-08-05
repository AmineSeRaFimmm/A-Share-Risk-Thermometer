from __future__ import annotations

from typing import Any

import pandas as pd


CORE_TAIL_EXECUTION_MODE = "T_TAIL_1450"
CORE_TAIL_RT_LOW = 64.0
CORE_TAIL_RT_HIGH = 76.0
CORE_TAIL_DD60_MAX = -0.065
CORE_TAIL_COVERAGE_MIN = 100.0
CORE_TAIL_DATA_QUALITY_MIN = 80.0
CORE_TAIL_REQUIRED_BREADTH_MODE = "STOCK_A"
CORE_TAIL_ALLOWED_DEGRADATIONS = frozenset({"QVIX_DELAYED", "QVIX_PROXY"})
CORE_TAIL_STABLE_SAMPLES = 3
CORE_TAIL_STABLE_MINUTES = 15
CORE_TAIL_MAX_SAMPLE_GAP_MINUTES = 20
CORE_TAIL_SAMPLE_TTL_MINUTES = 15
CORE_TAIL_WINDOW_START = "14:50"
CORE_TAIL_WINDOW_END = "15:00"


def _number(value: Any) -> float | None:
    numeric = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(numeric) else float(numeric)


def core_tail_condition_status(
    *,
    risk_temperature: Any,
    hs300_drawdown_60d: Any,
    model_confidence: Any,
    model_coverage_score: Any = None,
    model_data_quality_score: Any = None,
    model_missing_components: Any = None,
    breadth_mode: Any = None,
    breadth_quality: Any = None,
    temperature_mode: Any = None,
) -> dict[str, Any]:
    rt = _number(risk_temperature)
    dd60 = _number(hs300_drawdown_60d)
    confidence = _number(model_confidence)
    coverage = _number(model_coverage_score)
    data_quality = _number(model_data_quality_score)
    mode = str(temperature_mode or "")
    breadth_mode_value = str(breadth_mode or "")
    breadth_quality_value = str(breadth_quality or "")
    missing = [
        part
        for part in str(model_missing_components or "").split("|")
        if part and part.lower() != "nan"
    ]
    disallowed_missing = [part for part in missing if part not in CORE_TAIL_ALLOWED_DEGRADATIONS]
    intraday_mode = mode in {"NOWCAST", "ESTIMATED_CLOSE"}

    quality_checks = {
        "confidence_present": confidence is not None,
        "coverage": coverage is not None and coverage >= CORE_TAIL_COVERAGE_MIN,
        "data_quality": data_quality is not None and data_quality >= CORE_TAIL_DATA_QUALITY_MIN,
        "allowed_degradations": not disallowed_missing,
        "stock_breadth": (
            breadth_mode_value == CORE_TAIL_REQUIRED_BREADTH_MODE
            and breadth_quality_value.startswith("OK")
        ),
        "intraday_mode": intraday_mode,
    }
    strategy_checks = {
        "risk_temperature": rt is not None and CORE_TAIL_RT_LOW <= rt < CORE_TAIL_RT_HIGH,
        "hs300_drawdown_60d": dd60 is not None and dd60 <= CORE_TAIL_DD60_MAX,
    }
    data_valid = all(quality_checks.values())
    conditions_pass = all(strategy_checks.values())
    sample_state = "INVALID" if not data_valid else "PASS" if conditions_pass else "FAIL"
    return {
        "state": sample_state,
        "eligible": sample_state == "PASS",
        "data_valid": data_valid,
        "conditions_pass": conditions_pass,
        "checks": {**strategy_checks, **quality_checks},
        "quality_checks": quality_checks,
        "strategy_checks": strategy_checks,
        "invalid_reasons": [key for key, passed in quality_checks.items() if not passed],
        "values": {
            "risk_temperature": rt,
            "hs300_drawdown_60d": dd60,
            "model_confidence": confidence,
            "model_coverage_score": coverage,
            "model_data_quality_score": data_quality,
            "model_missing_components": "|".join(missing),
            "disallowed_missing_components": "|".join(disallowed_missing),
            "breadth_mode": breadth_mode_value,
            "breadth_quality": breadth_quality_value,
            "temperature_mode": mode,
        },
    }


def core_tail_strict_values_eligible(
    *,
    risk_temperature: Any,
    hs300_drawdown_60d: Any,
    model_confidence: Any,
) -> bool:
    """Historical alpha rule only; live data validity is confirmed separately."""
    rt = _number(risk_temperature)
    dd60 = _number(hs300_drawdown_60d)
    return bool(
        rt is not None
        and dd60 is not None
        and CORE_TAIL_RT_LOW <= rt < CORE_TAIL_RT_HIGH
        and dd60 <= CORE_TAIL_DD60_MAX
    )


def core_tail_policy_payload() -> dict[str, Any]:
    return {
        "id": "CORE_STRICT_TAIL_V1",
        "scope": "CORE_ONLY",
        "instrument": "510300",
        "execution_mode": CORE_TAIL_EXECUTION_MODE,
        "rt_low_inclusive": CORE_TAIL_RT_LOW,
        "rt_high_exclusive": CORE_TAIL_RT_HIGH,
        "hs300_drawdown_60d_max": CORE_TAIL_DD60_MAX,
        "quality_gate": {
            "coverage_score_min": CORE_TAIL_COVERAGE_MIN,
            "data_quality_score_min": CORE_TAIL_DATA_QUALITY_MIN,
            "required_breadth_mode": CORE_TAIL_REQUIRED_BREADTH_MODE,
            "allowed_degradations": sorted(CORE_TAIL_ALLOWED_DEGRADATIONS),
            "invalid_sample_behavior": "SKIP_WITHOUT_RESET",
        },
        "stable_samples": CORE_TAIL_STABLE_SAMPLES,
        "stable_min_span_minutes": CORE_TAIL_STABLE_MINUTES,
        "max_sample_gap_minutes": CORE_TAIL_MAX_SAMPLE_GAP_MINUTES,
        "sample_ttl_minutes": CORE_TAIL_SAMPLE_TTL_MINUTES,
        "execution_window": {
            "start": CORE_TAIL_WINDOW_START,
            "end": CORE_TAIL_WINDOW_END,
            "timezone": "Asia/Shanghai",
        },
        "fallback_execution": "T+1_OPEN",
        "other_signals_execution": "T+1_OPEN",
    }
