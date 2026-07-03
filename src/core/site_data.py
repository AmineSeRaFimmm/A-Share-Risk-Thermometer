from __future__ import annotations
import math
import pandas as pd
from src.core.risk_temperature import WEIGHTS, interpretation

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
    if "valuation_time" in out.columns:
        return out.sort_values("valuation_time").iloc[-1]
    return out.iloc[-1]

def _realtime_health(quality: str) -> str:
    if quality == "OK":
        return "OK"
    if quality.startswith("WARN"):
        return "WARN"
    if "BAD" in quality or quality.startswith("LOW"):
        return "LOW"
    return "WARN"

def latest_payload(risk: pd.DataFrame, avix_raw: pd.DataFrame, realtime: pd.DataFrame | None = None) -> dict:
    row = risk.sort_values("trade_date").iloc[-1]
    raw = avix_raw[avix_raw["trade_date"] == row.trade_date].iloc[-1] if not avix_raw.empty and row.trade_date in set(avix_raw["trade_date"]) else None
    realtime_row = _latest_realtime(realtime)
    realtime_quality = "LOW_NO_REALTIME_CHAIN" if realtime_row is None else str(realtime_row.get("quality", "OK"))
    comps = {
        "avix_percentile_2y": finite(row.avix_percentile_2y),
        "avix_zscore_1y": finite(row.avix_zscore_1y),
        "avix_5d_change": finite(row.avix_5d_change),
        "qvix_confirmation": finite(row.qvix_confirmation),
        "realized_vol": finite(row.realized_vol_percentile),
        "drawdown_pressure": finite(row.drawdown_pressure),
        "breadth_pressure": finite(row.market_breadth_pressure),
        "turnover_stress": finite(row.turnover_stress),
    }
    return {
        "trade_date": row.trade_date,
        "update_time": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
        "risk_temperature": finite(row.risk_temperature),
        "regime": row.regime,
        "regime_cn": row.regime_cn,
        "quality": row.quality,
        "components": comps,
        "market": {
            "hs300_close": finite(row.get("sh000300_close")),
            "hs300_ret_1d": None,
            "hs300_drawdown_60d": finite(row.get("sh000300_dd60")),
            "advancing_ratio": finite(row.get("advancing_ratio")),
            "big_down_ratio": finite(row.get("big_down_ratio")),
        },
        "avix": {
            "avix_clean_close": finite(row.get("avix_clean")),
            "avix_raw_close": finite(raw.avix_raw) if raw is not None else None,
            "avix_realtime_mid": None if realtime_row is None else finite(realtime_row.get("avix_mid")),
            "avix_realtime_quality": realtime_quality,
            "avix_realtime_note": None if realtime_row is None else realtime_row.get("note"),
            "avix_realtime_usable": realtime_quality == "OK",
            "avix_realtime_source": None if realtime_row is None else realtime_row.get("source"),
            "avix_percentile_2y": finite(row.avix_percentile_2y / 100),
            "quality": row.get("avix_quality", "OK"),
        },
        "interpretation": interpretation(float(row.risk_temperature), row.regime_cn, row),
    }

def history_payload(risk: pd.DataFrame, max_points: int = 900) -> list[dict]:
    cols = ["trade_date", "risk_temperature", "regime", "avix_clean", "qvix_close", "sh000300_close", "drawdown_pressure", "market_breadth_pressure"]
    out = risk.tail(max_points).copy()
    rows = []
    for r in out.itertuples():
        rows.append({
            "date": r.trade_date,
            "risk_temperature": finite(r.risk_temperature),
            "regime": r.regime,
            "avix_clean": finite(getattr(r, "avix_clean", None)),
            "qvix": finite(getattr(r, "qvix_close", None)),
            "hs300_close": finite(getattr(r, "sh000300_close", None)),
            "drawdown_pressure": finite(getattr(r, "drawdown_pressure", None)),
            "breadth_pressure": finite(getattr(r, "market_breadth_pressure", None)),
        })
    return rows

def components_payload(risk: pd.DataFrame) -> dict:
    row = risk.sort_values("trade_date").iloc[-1]
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
        "trade_date": row.trade_date,
        "components": [
            {"name": names[k], "score": finite(row[k]), "weight": w, "contribution": finite(row[k] * w)}
            for k, w in WEIGHTS.items()
        ],
    }

def audit_payload(risk: pd.DataFrame, realtime: pd.DataFrame | None = None) -> dict:
    row = risk.sort_values("trade_date").iloc[-1]
    quality = row.quality
    realtime_row = _latest_realtime(realtime)
    realtime_quality = "LOW_NO_REALTIME_CHAIN" if realtime_row is None else str(realtime_row.get("quality", "OK"))
    options_realtime_health = _realtime_health(realtime_quality)
    warnings = ([] if quality == "OK" else quality.split("|")) + ([] if realtime_quality == "OK" else realtime_quality.split("|"))
    return {
        "trade_date": row.trade_date,
        "data_health": {
            "options_history": "LOW" if "AVIX" in quality or "NO_CHAIN" in quality else "OK",
            "options_realtime": options_realtime_health,
            "qvix": "WARN" if "QVIX" in quality else "OK",
            "indices": "OK",
            "breadth": "WARN" if "BREADTH" in quality else "OK",
            "shibor": "WARN" if "RATE" in quality else "OK",
        },
        "realtime_avix": {
            "quality": realtime_quality,
            "usable": realtime_quality == "OK",
            "note": None if realtime_row is None else realtime_row.get("note"),
            "avix_mid": None if realtime_row is None else finite(realtime_row.get("avix_mid")),
        },
        "warnings": sorted(set(warnings)),
        "last_successful_update": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
    }
