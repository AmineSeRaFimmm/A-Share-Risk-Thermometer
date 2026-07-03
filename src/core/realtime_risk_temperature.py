from __future__ import annotations
import numpy as np
import pandas as pd
from src.core.risk_temperature import WEIGHTS, regime_for
from src.utils.quality import clip, merge_quality

AVIX_COMPONENTS = ["avix_percentile_2y", "avix_zscore_1y", "avix_5d_change"]
NON_AVIX_COMPONENTS = [k for k in WEIGHTS if k not in AVIX_COMPONENTS]


def _latest_row(df: pd.DataFrame | None, sort_col: str):
    if df is None or df.empty:
        return None
    out = df.copy()
    if sort_col in out.columns:
        return out.sort_values(sort_col).iloc[-1]
    return out.iloc[-1]


def _num(value):
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


def _avix_nowcast_components(risk: pd.DataFrame, realtime_avix: float, realtime_trade_date: str) -> dict[str, float]:
    hist = risk[["trade_date", "avix_clean"]].dropna(subset=["avix_clean"]).copy()
    hist["trade_date"] = hist["trade_date"].astype(str)
    hist["avix_clean"] = pd.to_numeric(hist["avix_clean"], errors="coerce")
    hist = hist.dropna(subset=["avix_clean"])
    hist = hist[hist["trade_date"] < str(realtime_trade_date)]
    hist = pd.concat([
        hist,
        pd.DataFrame([{"trade_date": str(realtime_trade_date), "avix_clean": float(realtime_avix)}]),
    ], ignore_index=True).sort_values("trade_date")

    s = hist["avix_clean"].astype(float).reset_index(drop=True)
    if s.empty:
        return {"avix_percentile_2y": 50.0, "avix_zscore_1y": 50.0, "avix_5d_change": 50.0}

    pct_window = s.tail(504).dropna()
    if len(pct_window) >= 20:
        avix_percentile_2y = float(pct_window.rank(pct=True).iloc[-1] * 100)
    else:
        avix_percentile_2y = 50.0

    z_window = s.tail(252).dropna()
    if len(z_window) >= 20 and float(z_window.std()) > 0:
        z = (float(z_window.iloc[-1]) - float(z_window.mean())) / float(z_window.std())
        avix_zscore_1y = clip(50 + 20 * z)
    else:
        avix_zscore_1y = 50.0

    if len(s) > 5 and pd.notna(s.iloc[-6]) and float(s.iloc[-6]) != 0:
        avix_5d_change = clip(50 + 200 * (float(s.iloc[-1]) / float(s.iloc[-6]) - 1))
    else:
        avix_5d_change = 50.0

    return {
        "avix_percentile_2y": round(float(avix_percentile_2y), 4),
        "avix_zscore_1y": round(float(avix_zscore_1y), 4),
        "avix_5d_change": round(float(avix_5d_change), 4),
    }


def realtime_nowcast_payload(risk: pd.DataFrame, realtime: pd.DataFrame | None) -> dict | None:
    """Build an intraday risk-temperature nowcast.

    The nowcast replaces only the three AVIX-derived factors with realtime AVIX.
    All non-AVIX factors stay anchored to the latest official close row, so the
    close-based historical risk series remains untouched.
    """
    official = _latest_row(risk, "trade_date")
    realtime_row = _latest_row(realtime, "valuation_time")
    if official is None or realtime_row is None:
        return None

    realtime_quality = str(realtime_row.get("quality", ""))
    realtime_trade_date = str(realtime_row.get("trade_date", ""))
    official_trade_date = str(official.get("trade_date", ""))
    realtime_avix = _num(realtime_row.get("avix_mid"))

    if realtime_quality != "OK" or pd.isna(realtime_avix) or float(realtime_avix) <= 0:
        return None
    if not realtime_trade_date or realtime_trade_date <= official_trade_date:
        return None

    components = _avix_nowcast_components(risk, float(realtime_avix), realtime_trade_date)
    for key in NON_AVIX_COMPONENTS:
        value = _num(official.get(key))
        components[key] = 50.0 if pd.isna(value) else float(value)

    temp = sum(float(components[k]) * WEIGHTS[k] for k in WEIGHTS)
    risk_temperature = round(clip(temp), 1)
    regime, regime_cn = regime_for(risk_temperature)
    baseline_quality = str(official.get("quality", "OK"))
    quality = merge_quality(["OK_NOWCAST", baseline_quality])

    return {
        "trade_date": realtime_trade_date,
        "risk_temperature": risk_temperature,
        "regime": regime,
        "regime_cn": regime_cn,
        "quality": quality,
        "components": components,
        "baseline_trade_date": official_trade_date,
        "official_risk_temperature": float(official.get("risk_temperature")),
        "realtime_avix": float(realtime_avix),
        "realtime_valuation_time": realtime_row.get("valuation_time"),
        "method": "Realtime AVIX factors + previous official close non-AVIX factors",
    }
