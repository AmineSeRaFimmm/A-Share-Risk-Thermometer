from __future__ import annotations

from typing import Any

import pandas as pd

from src.utils.config import load_weights


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
CORE_TAIL_PASS_STATES = frozenset({"PASS", "DEGRADED_PASS"})
CORE_TAIL_FAIL_STATES = frozenset({"FAIL", "DEGRADED_FAIL"})
CORE_TAIL_SKIP_STATES = frozenset({"INDETERMINATE", "INVALID"})

_WEIGHTS = load_weights()
_UNCERTAINTY_GROUPS = {
    "AVIX": (
        ("avix_percentile_2y", "avix_percentile_2y"),
        ("avix_zscore_1y", "avix_zscore_1y"),
        ("avix_5d_change", "avix_5d_change"),
    ),
    "QVIX": (("qvix_confirmation", "qvix_confirmation"),),
    "REALIZED_VOL": (("realized_vol", "realized_vol_percentile"),),
    "DRAWDOWN": (("drawdown_pressure", "drawdown_pressure"),),
    "BREADTH": (("breadth_pressure", "market_breadth_pressure"),),
    "TURNOVER": (("turnover_stress", "turnover_stress"),),
}
_MISSING_TO_GROUPS = {
    "AVIX": ("AVIX",),
    "QVIX": ("QVIX",),
    "REALIZED_VOL": ("REALIZED_VOL",),
    "DRAWDOWN": ("DRAWDOWN",),
    "BREADTH": ("BREADTH",),
    "STOCK_BREADTH": ("BREADTH",),
    "TURNOVER": ("TURNOVER",),
    "REALTIME_INDEX_FACTORS": ("REALIZED_VOL", "DRAWDOWN", "TURNOVER"),
}


def _number(value: Any) -> float | None:
    numeric = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(numeric) else float(numeric)


def _bounded_temperature(
    risk_temperature: float,
    groups: list[str],
    component_scores: Any,
) -> dict[str, Any] | None:
    if not isinstance(component_scores, dict):
        return None
    weighted_temperature = 0.0
    seen_score_keys: set[str] = set()
    for entries in _UNCERTAINTY_GROUPS.values():
        for score_key, weight_key in entries:
            if score_key in seen_score_keys:
                continue
            score = _number(component_scores.get(score_key))
            if score is None:
                return None
            weighted_temperature += score * float(_WEIGHTS[weight_key])
            seen_score_keys.add(score_key)
    if abs(weighted_temperature - risk_temperature) > 0.2:
        return None
    current_contribution = 0.0
    uncertain_weight = 0.0
    for group in groups:
        for score_key, weight_key in _UNCERTAINTY_GROUPS[group]:
            score = _number(component_scores.get(score_key))
            if score is None:
                return None
            weight = float(_WEIGHTS[weight_key])
            current_contribution += score * weight
            uncertain_weight += weight
    lower = max(0.0, risk_temperature - current_contribution)
    upper = min(100.0, lower + uncertain_weight * 100.0)
    return {
        "groups": groups,
        "weight": round(uncertain_weight, 4),
        "risk_temperature_lower": round(lower, 4),
        "risk_temperature_upper": round(upper, 4),
    }


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
    component_scores: Any = None,
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
    quality_failures = {key for key, passed in quality_checks.items() if not passed}
    uncertain_groups = sorted({
        group
        for part in disallowed_missing
        for group in _MISSING_TO_GROUPS.get(part, ())
    })
    unknown_missing = [part for part in disallowed_missing if part not in _MISSING_TO_GROUPS]
    structural_valid = (
        confidence is not None
        and rt is not None
        and dd60 is not None
        and intraday_mode
    )
    explained_failures = {"coverage", "allowed_degradations"}
    if "BREADTH" in uncertain_groups:
        explained_failures.add("stock_breadth")
    breadth_metadata_consistent = (
        "BREADTH" not in uncertain_groups or not quality_checks["stock_breadth"]
    )
    bounded = None
    if (
        not data_valid
        and structural_valid
        and uncertain_groups
        and not unknown_missing
        and breadth_metadata_consistent
        and quality_failures.issubset(explained_failures)
    ):
        bounded = _bounded_temperature(rt, uncertain_groups, component_scores)

    if data_valid:
        sample_state = "PASS" if conditions_pass else "FAIL"
        decision_basis = "FULL_QUALITY"
    elif bounded is None:
        sample_state = "INVALID"
        decision_basis = "UNBOUNDED_INVALID"
    else:
        lower = float(bounded["risk_temperature_lower"])
        upper = float(bounded["risk_temperature_upper"])
        rt_always_passes = lower >= CORE_TAIL_RT_LOW and upper < CORE_TAIL_RT_HIGH
        rt_always_fails = upper < CORE_TAIL_RT_LOW or lower >= CORE_TAIL_RT_HIGH
        dd_passes = dd60 <= CORE_TAIL_DD60_MAX
        if dd_passes and rt_always_passes:
            sample_state = "DEGRADED_PASS"
        elif not dd_passes or rt_always_fails:
            sample_state = "DEGRADED_FAIL"
        else:
            sample_state = "INDETERMINATE"
        decision_basis = "BOUNDED_DEGRADATION"
    return {
        "state": sample_state,
        "eligible": sample_state in CORE_TAIL_PASS_STATES,
        "definitive_fail": sample_state in CORE_TAIL_FAIL_STATES,
        "skip_without_reset": sample_state in CORE_TAIL_SKIP_STATES,
        "degraded": sample_state.startswith("DEGRADED_") or sample_state == "INDETERMINATE",
        "decision_basis": decision_basis,
        "uncertainty": bounded,
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
        "id": "CORE_STRICT_TAIL_V2",
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
            "bounded_degradation": {
                "method": "replace identified missing factor groups across their full 0-100 score range",
                "pass_behavior": "COUNT_AS_PASS",
                "fail_behavior": "RESET_STREAK",
                "indeterminate_behavior": "SKIP_WITHOUT_RESET",
            },
        },
        "sample_states": {
            "PASS": "full-quality conditions pass",
            "FAIL": "full-quality conditions fail",
            "DEGRADED_PASS": "missing-factor bounds still always pass",
            "DEGRADED_FAIL": "missing-factor bounds still always fail",
            "INDETERMINATE": "missing-factor bounds cross a decision boundary",
            "INVALID": "structurally unusable or unbounded data fault",
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
