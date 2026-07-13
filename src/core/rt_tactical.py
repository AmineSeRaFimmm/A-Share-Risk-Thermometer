"""Research-backed risk-temperature tactical study (not investment advice).

Default rule from CSI300 RT research:
  60 <= risk_temperature < 75
  AND HS300 60-day drawdown <= -5%
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.utils.config import load_thresholds

ENTRY_LOW = 60.0
ENTRY_HIGH = 75.0
DRAWDOWN_CONFIRM = -0.05


def _finite(value: object) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if pd.isna(out):
        return None
    return out


def build_rt_tactical_payload(
    risk_components: pd.DataFrame,
    index_history: pd.DataFrame,
) -> dict[str, Any]:
    disclaimer = (
        "研究观察信号，来自历史回测（推荐区间 60-75 + 60日回撤确认）。"
        "不是买卖建议，也不是生产交易指令；与 S3/S4（AVIX 规则）相互独立。"
    )
    rule_summary = f"{ENTRY_LOW:g} ≤ RT < {ENTRY_HIGH:g} 且 沪深300 60日回撤 ≤ {DRAWDOWN_CONFIRM:.0%}"
    base = {
        "status": "ready",
        "rule_id": "research_rt60_75_dd5",
        "rule_summary": rule_summary,
        "entry_low": ENTRY_LOW,
        "entry_high": ENTRY_HIGH,
        "drawdown_confirm": DRAWDOWN_CONFIRM,
        "source": "research/output/csi300_risk_temp_strategy_report.md",
        "disclaimer": disclaimer,
        "latest": {},
    }
    if risk_components is None or risk_components.empty:
        base["status"] = "empty"
        return base

    risk = risk_components.copy()
    risk["trade_date"] = pd.to_datetime(risk["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    risk = risk.dropna(subset=["trade_date"]).sort_values("trade_date")
    if risk.empty:
        base["status"] = "empty"
        return base

    latest = risk.iloc[-1]
    temp = _finite(latest.get("risk_temperature"))
    if temp is None:
        temp = _finite(latest.get("avix_percentile_2y"))  # fallback unlikely

    dd60 = None
    if index_history is not None and not index_history.empty:
        hs = index_history[index_history["symbol"].astype(str) == "sh000300"].copy()
        if not hs.empty:
            hs = hs.sort_values("date")
            hs["close"] = pd.to_numeric(hs["close"], errors="coerce")
            hs["dd60"] = hs["close"] / hs["close"].rolling(60, min_periods=10).max() - 1
            hs["trade_date"] = pd.to_datetime(hs["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            matched = hs[hs["trade_date"] == latest["trade_date"]]
            if not matched.empty:
                dd60 = _finite(matched.iloc[-1]["dd60"])
            elif not hs.empty:
                dd60 = _finite(hs.iloc[-1]["dd60"])

    in_band = temp is not None and ENTRY_LOW <= temp < ENTRY_HIGH
    dd_ok = dd60 is not None and dd60 <= DRAWDOWN_CONFIRM
    active = bool(in_band and dd_ok)

    if active:
        status = "WATCH_RESEARCH"
        status_cn = "研究关注"
    elif in_band:
        status = "IN_BAND_NO_DD"
        status_cn = "落在区间但回撤未确认"
    elif temp is not None and temp >= ENTRY_HIGH:
        status = "ABOVE_BAND"
        status_cn = "高于研究上沿"
    elif temp is not None and temp < ENTRY_LOW:
        status = "BELOW_BAND"
        status_cn = "低于研究下沿"
    else:
        status = "UNKNOWN"
        status_cn = "数据不足"

    thr = load_thresholds()
    base["latest"] = {
        "trade_date": str(latest.get("trade_date")),
        "risk_temperature": None if temp is None else round(temp, 1),
        "drawdown_60d": None if dd60 is None else round(dd60, 4),
        "in_band": in_band,
        "drawdown_confirmed": dd_ok,
        "active": active,
        "status": status,
        "status_cn": status_cn,
        "avix_warning_level": thr["fixed_warning_level"],
        "avix_panic_level": thr["fixed_panic_level"],
    }
    return base
