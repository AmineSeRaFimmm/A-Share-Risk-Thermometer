from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.storage.csv_store import read_csv, write_csv


HISTORY_COLUMNS = [
    "trade_date",
    "sampled_at",
    "sample_minute",
    "risk_temperature",
    "temperature_mode",
    "temperature_mode_cn",
    "is_final",
    "quality",
    "model_confidence",
    "source_update_time",
]

VALID_MODES = {"NOWCAST", "CLOSE_PENDING", "ESTIMATED_CLOSE", "OFFICIAL_CLOSE"}


def _empty_history() -> pd.DataFrame:
    return pd.DataFrame(columns=HISTORY_COLUMNS)


def _shanghai_timestamp(value: Any) -> pd.Timestamp | None:
    try:
        timestamp = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("Asia/Shanghai")
    return timestamp.tz_convert("Asia/Shanghai")


def _confidence_score(latest: dict[str, Any]) -> float | None:
    confidence = latest.get("model_confidence")
    value = confidence.get("score") if isinstance(confidence, dict) else confidence
    numeric = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(numeric) else round(float(numeric), 2)


def _normalized_history(history: pd.DataFrame | None) -> pd.DataFrame:
    if history is None or history.empty:
        return _empty_history()
    frame = history.copy()
    for column in HISTORY_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[HISTORY_COLUMNS]
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    frame["sampled_at"] = frame["sampled_at"].astype(str)
    frame["sample_minute"] = frame["sample_minute"].astype(str)
    frame["risk_temperature"] = pd.to_numeric(frame["risk_temperature"], errors="coerce")
    frame["model_confidence"] = pd.to_numeric(frame["model_confidence"], errors="coerce")
    frame["is_final"] = frame["is_final"].astype(str).str.lower().isin({"true", "1"})
    return frame.dropna(subset=["risk_temperature"])


def update_intraday_temperature_history(
    history: pd.DataFrame | None,
    latest: dict[str, Any],
) -> tuple[pd.DataFrame, bool]:
    """Record one actual pipeline refresh, returning history and whether it changed.

    Intraday observations are idempotent within a Beijing-time minute. Official close
    is a single endpoint per trade date. A stale rebuild on a later calendar date is
    deliberately ignored so weekends and historical rebuilds cannot invent samples.
    """
    frame = _normalized_history(history)
    trade_date = str(latest.get("trade_date") or "")[:10]
    sampled_at = _shanghai_timestamp(latest.get("update_time"))
    temperature = pd.to_numeric(latest.get("risk_temperature"), errors="coerce")
    mode = str(latest.get("temperature_mode") or "")

    if (
        len(trade_date) != 10
        or sampled_at is None
        or sampled_at.strftime("%Y-%m-%d") != trade_date
        or pd.isna(temperature)
        or not 0 <= float(temperature) <= 100
        or mode not in VALID_MODES
    ):
        return frame, False

    is_final = bool(latest.get("is_final")) or mode == "OFFICIAL_CLOSE"
    sampled_iso = sampled_at.isoformat(timespec="seconds")
    sample_minute = sampled_at.strftime("%Y-%m-%dT%H:%M")
    new_row = {
        "trade_date": trade_date,
        "sampled_at": sampled_iso,
        "sample_minute": sample_minute,
        "risk_temperature": round(float(temperature), 2),
        "temperature_mode": mode,
        "temperature_mode_cn": str(latest.get("temperature_mode_cn") or mode),
        "is_final": is_final,
        "quality": str(latest.get("quality") or ""),
        "model_confidence": _confidence_score(latest),
        "source_update_time": sampled_iso,
    }

    same_date = frame["trade_date"].eq(trade_date)
    if is_final:
        existing = frame[same_date & frame["is_final"]]
        if not existing.empty:
            previous = existing.sort_values("sampled_at").iloc[-1]
            unchanged = (
                float(previous["risk_temperature"]) == new_row["risk_temperature"]
                and str(previous["quality"]) == new_row["quality"]
            )
            if unchanged:
                return frame, False
        frame = frame.loc[
            ~(same_date & (frame["is_final"] | frame["sample_minute"].eq(sample_minute)))
        ].copy()
    else:
        frame = frame.loc[~(same_date & frame["sample_minute"].eq(sample_minute))].copy()

    frame = pd.concat([frame, pd.DataFrame([new_row])], ignore_index=True)
    frame = frame.sort_values(["trade_date", "sampled_at", "is_final"]).reset_index(drop=True)
    return frame[HISTORY_COLUMNS], True


def intraday_temperature_payload(
    history: pd.DataFrame | None,
    preferred_trade_date: str | None = None,
) -> dict[str, Any]:
    frame = _normalized_history(history)
    if frame.empty:
        return {
            "status": "no_samples",
            "trade_date": None,
            "sample_count": 0,
            "has_final": False,
            "rows": [],
            "available_dates": [],
            "methodology": _methodology(),
        }

    available_dates = sorted(frame["trade_date"].dropna().astype(str).unique().tolist())
    requested = str(preferred_trade_date or "")[:10]
    trade_date = requested if requested in available_dates else available_dates[-1]
    day = frame[frame["trade_date"].eq(trade_date)].sort_values("sampled_at")
    rows = []
    for row in day.itertuples(index=False):
        rows.append({
            "sampled_at": row.sampled_at,
            "time": str(row.sampled_at)[11:16],
            "risk_temperature": round(float(row.risk_temperature), 2),
            "temperature_mode": row.temperature_mode,
            "temperature_mode_cn": row.temperature_mode_cn,
            "is_final": bool(row.is_final),
            "quality": row.quality,
            "model_confidence": None if pd.isna(row.model_confidence) else float(row.model_confidence),
        })
    return {
        "status": "ok",
        "trade_date": trade_date,
        "sample_count": len(rows),
        "has_final": any(item["is_final"] for item in rows),
        "first_sample_at": rows[0]["sampled_at"],
        "last_sample_at": rows[-1]["sampled_at"],
        "rows": rows,
        "available_dates": available_dates,
        "methodology": _methodology(),
    }


def record_intraday_temperature(
    latest: dict[str, Any],
    history_path: Path,
) -> tuple[dict[str, Any], bool]:
    history, changed = update_intraday_temperature_history(read_csv(history_path), latest)
    if changed or not history_path.exists():
        write_csv(history, history_path)
    return intraday_temperature_payload(history, latest.get("trade_date")), changed


def _methodology() -> dict[str, str]:
    return {
        "sampling": "One point per backend temperature calculation refresh; browser polling never creates points.",
        "deduplication": "Intraday refreshes are idempotent within one Beijing-time minute.",
        "official_close": "Official close is stored as the single final endpoint for each trade date.",
        "stale_guard": "A rebuild is recorded only when its Beijing calendar date equals the market trade date.",
    }
