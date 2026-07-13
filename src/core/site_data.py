from __future__ import annotations
import math
import pandas as pd
from src.core.risk_temperature import WEIGHTS, interpretation
from src.core.realtime_risk_temperature import realtime_nowcast_payload
from src.core.strategy_s3_s4 import latest_strategy_payload


def finite(v):
    if v is None:
        return None
    try:
        if math.isnan(float(v)):
            return None
        return round(float(v), 4)
    except Exception:
        return v


def _latest_realtime(realtime: pd.DataFrame | None):
    if realtime is None or realtime.empty:
        return None
    out = realtime.copy()
    return out.sort_values("valuation_time").iloc[-1] if "valuation_time" in out.columns else out.iloc[-1]


def _realtime_health(quality: str) -> str:
    if quality == "OK":
        return "OK"
    if quality.startswith("WARN"):
        return "WARN"
    if "BAD" in quality or quality.startswith("LOW"):
        return "LOW"
    return "WARN"


def _official_components(row: pd.Series) -> dict:
    return {
        "avix_percentile_2y": row.avix_percentile_2y,
        "avix_zscore_1y": row.avix_zscore_1y,
        "avix_5d_change": row.avix_5d_change,
        "qvix_confirmation": row.qvix_confirmation,
        "realized_vol_percentile": row.realized_vol_percentile,
        "drawdown_pressure": row.drawdown_pressure,
        "market_breadth_pressure": row.market_breadth_pressure,
        "turnover_stress": row.turnover_stress,
    }


def _latest_component_summary(comps: dict) -> dict:
    return {
        "avix_percentile_2y": finite(comps.get("avix_percentile_2y")),
        "avix_zscore_1y": finite(comps.get("avix_zscore_1y")),
        "avix_5d_change": finite(comps.get("avix_5d_change")),
        "qvix_confirmation": finite(comps.get("qvix_confirmation")),
        "realized_vol": finite(comps.get("realized_vol_percentile")),
        "drawdown_pressure": finite(comps.get("drawdown_pressure")),
        "breadth_pressure": finite(comps.get("market_breadth_pressure")),
        "turnover_stress": finite(comps.get("turnover_stress")),
    }


def _model_confidence_summary(row: pd.Series, quality: str) -> dict:
    confidence = row.get("model_confidence")
    missing = row.get("model_missing_components")
    if confidence is None or pd.isna(confidence):
        flags = str(quality or "").split("|")
        confidence = 100.0
        if any("QVIX" in f for f in flags):
            confidence -= 12.0
        if any("BREADTH_MISSING" in f for f in flags):
            confidence -= 10.0
        if any("BREADTH_PROXY" in f for f in flags):
            confidence -= 4.0
        if any("AVIX" in f and ("LOW" in f or "BAD" in f) for f in flags):
            confidence -= 50.0
        missing = "|".join(
            part for part, present in [
                ("QVIX", any("QVIX" in f for f in flags)),
                ("BREADTH", any("BREADTH_MISSING" in f for f in flags)),
                ("STOCK_BREADTH", any("BREADTH_PROXY" in f for f in flags)),
            ] if present
        )
    confidence = max(0.0, min(100.0, float(confidence)))
    if confidence >= 90:
        grade = "HIGH"
    elif confidence >= 75:
        grade = "MEDIUM"
    else:
        grade = "LOW"
    return {
        "score": finite(confidence),
        "grade": grade,
        "missing_components": "" if pd.isna(missing) else str(missing or ""),
    }


def _confidence_label(confidence: dict) -> str:
    score = confidence.get("score")
    grade = confidence.get("grade")
    if score is None:
        return "--"
    grade_cn = {"HIGH": "高", "MEDIUM": "中", "LOW": "低"}.get(str(grade), str(grade))
    return f"{score:.1f} / {grade_cn}"


def _breadth_mode(row: pd.Series) -> str:
    quality = str(row.get("breadth_quality") or row.get("quality") or "")
    if "WARN_BREADTH_PROXY" in quality:
        return "INDEX_PROXY"
    if "WARN_BREADTH_MISSING" in quality:
        return "MISSING"
    if pd.notna(row.get("breadth_pressure")) or pd.notna(row.get("market_breadth_pressure")):
        if "OK" in quality or quality == "" or "PROXY" not in quality:
            # Empty quality with values usually means stock snapshot OK.
            if "PROXY" in quality:
                return "INDEX_PROXY"
            return "STOCK_A"
    return "UNKNOWN"


def _breadth_mode_cn(row: pd.Series) -> str:
    return {
        "STOCK_A": "全A个股宽度",
        "INDEX_PROXY": "宽基指数代理",
        "MISSING": "宽度缺失",
        "UNKNOWN": "宽度未知",
    }.get(_breadth_mode(row), "宽度未知")


def _active_view(risk: pd.DataFrame, realtime: pd.DataFrame | None):
    official = risk.sort_values("trade_date").iloc[-1]
    nowcast = realtime_nowcast_payload(risk, realtime)
    if nowcast:
        comps = nowcast["components"]
        return nowcast, comps, pd.Series(comps)
    comps = _official_components(official)
    return None, comps, official


def _latest_estimated_row(nowcast_history: dict | None):
    rows = (nowcast_history or {}).get("rows", [])
    if not rows:
        return None
    return sorted(rows, key=lambda item: str(item.get("date") or item.get("trade_date")))[-1]


def latest_payload(
    risk: pd.DataFrame,
    avix_raw: pd.DataFrame,
    realtime: pd.DataFrame | None = None,
    nowcast_history: dict | None = None,
) -> dict:
    row = risk.sort_values("trade_date").iloc[-1]
    raw = avix_raw[avix_raw["trade_date"] == row.trade_date].iloc[-1] if not avix_raw.empty and row.trade_date in set(avix_raw["trade_date"]) else None
    realtime_row = _latest_realtime(realtime)
    realtime_quality = "LOW_NO_REALTIME_CHAIN" if realtime_row is None else str(realtime_row.get("quality", "OK"))
    nowcast, comps, interp_row = _active_view(risk, realtime)
    estimate = _latest_estimated_row(nowcast_history)
    estimate_date = None if estimate is None else str(estimate.get("date") or estimate.get("trade_date"))
    use_estimate = estimate is not None and estimate_date and estimate_date > str(row.trade_date)
    if use_estimate:
        comps = estimate.get("components") or comps
        interp_row = pd.Series(comps)
        temp = estimate.get("risk_temperature_estimated")
        regime = estimate.get("regime")
        regime_cn = estimate.get("regime_cn")
        quality = estimate.get("quality")
        trade_date = estimate_date
        confidence = {
            "score": finite(estimate.get("model_confidence")),
            "grade": "MEDIUM" if float(estimate.get("model_confidence") or 0) >= 75 else "LOW",
            "missing_components": str(estimate.get("model_missing_components") or ""),
        }
    else:
        temp = nowcast["risk_temperature"] if nowcast else row.risk_temperature
        regime = nowcast["regime"] if nowcast else row.regime
        regime_cn = nowcast["regime_cn"] if nowcast else row.regime_cn
        quality = nowcast["quality"] if nowcast else row.quality
        trade_date = nowcast["trade_date"] if nowcast else row.trade_date
        confidence = _model_confidence_summary(row, quality)
    return {
        "trade_date": trade_date,
        "update_time": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
        "risk_temperature": finite(temp),
        "regime": regime,
        "regime_cn": regime_cn,
        "quality": quality,
        "model_confidence": confidence,
        "model_confidence_label": _confidence_label(confidence),
        "temperature_mode": "ESTIMATED_CLOSE" if use_estimate else "NOWCAST" if nowcast else "OFFICIAL_CLOSE",
        "temperature_mode_cn": "估算收盘" if use_estimate else "盘中估算" if nowcast else "收盘正式",
        "is_final": not (use_estimate or nowcast),
        "components": _latest_component_summary(comps),
        "market": {
            "hs300_close": finite(row.get("sh000300_close")),
            "hs300_ret_1d": None,
            "hs300_drawdown_60d": finite(row.get("sh000300_dd60")),
            "advancing_ratio": finite(row.get("advancing_ratio")),
            "big_down_ratio": finite(row.get("big_down_ratio")),
            "as_of_trade_date": row.trade_date,
            "breadth_mode": _breadth_mode(row),
            "breadth_mode_cn": _breadth_mode_cn(row),
            "breadth_quality": str(row.get("breadth_quality") or ""),
        },
        "avix": {
            "avix_clean_close": finite(row.get("avix_clean")),
            "avix_raw_close": finite(raw.avix_raw) if raw is not None else None,
            "avix_realtime_mid": None if realtime_row is None else finite(realtime_row.get("avix_mid")),
            "avix_realtime_quality": realtime_quality,
            "avix_realtime_note": None if realtime_row is None else realtime_row.get("note"),
            "avix_realtime_usable": realtime_quality == "OK",
            "avix_realtime_source": None if realtime_row is None else realtime_row.get("source"),
            "avix_percentile_2y": finite(comps.get("avix_percentile_2y") / 100 if comps.get("avix_percentile_2y") is not None else None),
            "quality": row.get("avix_quality", "OK"),
        },
        "official_close": {
            "trade_date": row.trade_date,
            "risk_temperature": finite(row.risk_temperature),
            "regime": row.regime,
            "regime_cn": row.regime_cn,
            "quality": row.quality,
            "model_confidence": _model_confidence_summary(row, row.quality),
        },
        "nowcast": {
            "trade_date": trade_date,
            "risk_temperature": finite(temp),
            "regime": regime,
            "regime_cn": regime_cn,
            "quality": quality,
            "baseline_trade_date": estimate.get("baseline_trade_date") if use_estimate else nowcast["baseline_trade_date"] if nowcast else row.trade_date,
            "official_risk_temperature": finite(row.risk_temperature),
            "realtime_avix": finite(estimate.get("avix_realtime_mid")) if use_estimate else finite(nowcast["realtime_avix"]) if nowcast else None,
            "realtime_valuation_time": estimate.get("realtime_valuation_time") if use_estimate else nowcast["realtime_valuation_time"] if nowcast else None,
            "method": "Estimated close from realtime AVIX plus available close-based non-AVIX factors" if use_estimate else nowcast["method"] if nowcast else "Official close",
        } if (use_estimate or nowcast) else None,
        "interpretation": interpretation(float(temp), regime_cn, interp_row),
    }


def history_payload(risk: pd.DataFrame, max_points: int = 900) -> list[dict]:
    out = risk.tail(max_points).copy()
    return [{
        "date": r.trade_date,
        "risk_temperature": finite(r.risk_temperature),
        "regime": r.regime,
        "avix_clean": finite(getattr(r, "avix_clean", None)),
        "qvix": finite(getattr(r, "qvix_close", None)),
        "qvix_replica": finite(getattr(r, "qvix_replica", None)),
        "qvix_replica_quality": getattr(r, "qvix_replica_quality", None),
        "hs300_close": finite(getattr(r, "sh000300_close", None)),
        "drawdown_pressure": finite(getattr(r, "drawdown_pressure", None)),
        "breadth_pressure": finite(getattr(r, "market_breadth_pressure", None)),
        "model_confidence": finite(getattr(r, "model_confidence", None)),
    } for r in out.itertuples()]


def components_payload(risk: pd.DataFrame, realtime: pd.DataFrame | None = None, nowcast_history: dict | None = None) -> dict:
    row = risk.sort_values("trade_date").iloc[-1]
    nowcast, comps, _ = _active_view(risk, realtime)
    estimate = _latest_estimated_row(nowcast_history)
    estimate_date = None if estimate is None else str(estimate.get("date") or estimate.get("trade_date"))
    use_estimate = estimate is not None and estimate_date and estimate_date > str(row.trade_date)
    if use_estimate:
        comps = estimate.get("components") or comps
    names = {
        "avix_percentile_2y": "AVIX两年分位",
        "avix_zscore_1y": "AVIX Z-score",
        "avix_5d_change": "AVIX 5日变化",
        "qvix_confirmation": "QVIX确认",
        "realized_vol_percentile": "实现波动率",
        "drawdown_pressure": "回撤压力",
        "market_breadth_pressure": "市场宽度",
        "turnover_stress": "成交压力",
    }
    return {
        "trade_date": estimate_date if use_estimate else nowcast["trade_date"] if nowcast else row.trade_date,
        "temperature_mode": "ESTIMATED_CLOSE" if use_estimate else "NOWCAST" if nowcast else "OFFICIAL_CLOSE",
        "components": [
            {"name": names[k], "score": finite(comps.get(k)), "weight": w, "contribution": finite(comps.get(k) * w if comps.get(k) is not None else None)}
            for k, w in WEIGHTS.items()
        ],
    }


def audit_payload(risk: pd.DataFrame, realtime: pd.DataFrame | None = None, nowcast_history: dict | None = None) -> dict:
    row = risk.sort_values("trade_date").iloc[-1]
    quality = row.quality
    realtime_row = _latest_realtime(realtime)
    realtime_quality = "LOW_NO_REALTIME_CHAIN" if realtime_row is None else str(realtime_row.get("quality", "OK"))
    nowcast = realtime_nowcast_payload(risk, realtime)
    estimate = _latest_estimated_row(nowcast_history)
    estimate_date = None if estimate is None else str(estimate.get("date") or estimate.get("trade_date"))
    use_estimate = estimate is not None and estimate_date and estimate_date > str(row.trade_date)
    active_quality = estimate.get("quality") if use_estimate else nowcast["quality"] if nowcast else row.quality
    warnings = ([] if active_quality in ("OK", "OK_NOWCAST") else str(active_quality).split("|")) + ([] if realtime_quality == "OK" else realtime_quality.split("|"))
    if not use_estimate and nowcast and nowcast["quality"] != "OK_NOWCAST":
        warnings += nowcast["quality"].split("|")
    confidence = (
        {
            "score": finite(estimate.get("model_confidence")),
            "grade": "MEDIUM" if float(estimate.get("model_confidence") or 0) >= 75 else "LOW",
            "missing_components": str(estimate.get("model_missing_components") or ""),
        }
        if use_estimate else _model_confidence_summary(row, nowcast["quality"] if nowcast else row.quality)
    )
    return {
        "trade_date": estimate_date if use_estimate else nowcast["trade_date"] if nowcast else row.trade_date,
        "temperature_mode": "ESTIMATED_CLOSE" if use_estimate else "NOWCAST" if nowcast else "OFFICIAL_CLOSE",
        "data_health": {
            "options_history": "WARN" if use_estimate else "LOW" if "AVIX" in quality or "NO_CHAIN" in quality else "OK",
            "options_realtime": _realtime_health(realtime_quality),
            "qvix": "WARN" if "QVIX" in str(active_quality) else "OK",
            "indices": "OK",
            "breadth": "WARN" if "BREADTH" in str(active_quality) else "OK",
            "shibor": "WARN" if "RATE" in quality else "OK",
        },
        "realtime_avix": {
            "quality": realtime_quality,
            "usable": realtime_quality == "OK",
            "note": None if realtime_row is None else realtime_row.get("note"),
            "avix_mid": None if realtime_row is None else finite(realtime_row.get("avix_mid")),
        },
        "nowcast": {
            "active": True,
            "quality": active_quality,
            "risk_temperature": finite(estimate.get("risk_temperature_estimated")) if use_estimate else finite(nowcast["risk_temperature"]) if nowcast else finite(row.risk_temperature),
            "baseline_trade_date": estimate.get("baseline_trade_date") if use_estimate else nowcast["baseline_trade_date"] if nowcast else row.trade_date,
            "method": "Estimated close from realtime AVIX plus available close-based non-AVIX factors" if use_estimate else nowcast["method"] if nowcast else "Official close",
        } if (use_estimate or nowcast) else None,
        "model_confidence": confidence,
        "warnings": sorted(set(warnings)),
        "last_successful_update": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
    }


def strategy_payload(strategy: pd.DataFrame) -> dict:
    return latest_strategy_payload(strategy)
