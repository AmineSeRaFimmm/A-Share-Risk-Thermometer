from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.core.core_tail_policy import (
    CORE_TAIL_FAIL_STATES,
    CORE_TAIL_MAX_SAMPLE_GAP_MINUTES,
    CORE_TAIL_PASS_STATES,
    CORE_TAIL_SAMPLE_TTL_MINUTES,
    CORE_TAIL_SKIP_STATES,
    CORE_TAIL_STABLE_MINUTES,
    CORE_TAIL_STABLE_SAMPLES,
    CORE_TAIL_WINDOW_END,
    CORE_TAIL_WINDOW_START,
    core_tail_condition_status,
    core_tail_policy_payload,
)
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
    "model_coverage_score",
    "model_data_quality_score",
    "model_missing_components",
    "hs300_drawdown_60d",
    "breadth_mode",
    "breadth_quality",
    "breadth_source",
    "breadth_observed",
    "plot_eligible",
    "avix_percentile_2y",
    "avix_zscore_1y",
    "avix_5d_change",
    "qvix_confirmation",
    "realized_vol",
    "drawdown_pressure",
    "breadth_pressure",
    "turnover_stress",
    "source_update_time",
]

VALID_MODES = {"NOWCAST", "CLOSE_PENDING", "ESTIMATED_CLOSE", "OFFICIAL_CLOSE"}
INVALID_REASON_CN = {
    "confidence_present": "置信度缺失",
    "coverage": "模型覆盖不足",
    "data_quality": "数据质量不足",
    "allowed_degradations": "存在关键因子缺失",
    "stock_breadth": "全A宽度无效",
    "intraday_mode": "非盘中估算",
}
SAMPLE_STATE_CN = {
    "PASS": "完整数据通过",
    "FAIL": "完整数据不通过",
    "DEGRADED_PASS": "缺失数据下仍稳健通过",
    "DEGRADED_FAIL": "缺失数据下仍稳健不通过",
    "INDETERMINATE": "缺失数据导致边界不确定",
    "INVALID": "数据结构无效",
}


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


def _confidence_value(latest: dict[str, Any], key: str) -> float | None:
    confidence = latest.get("model_confidence")
    value = confidence.get(key) if isinstance(confidence, dict) else None
    numeric = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(numeric) else round(float(numeric), 2)


def _market_value(latest: dict[str, Any], key: str) -> float | None:
    market = latest.get("market")
    value = market.get(key) if isinstance(market, dict) else None
    numeric = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(numeric) else float(numeric)


def _market_text(latest: dict[str, Any], key: str) -> str:
    market = latest.get("market")
    value = market.get(key) if isinstance(market, dict) else None
    return str(value or "")


def _optional_bool(value: Any) -> bool | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _component_value(latest: dict[str, Any], key: str) -> float | None:
    components = latest.get("components")
    value = components.get(key) if isinstance(components, dict) else None
    numeric = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(numeric) else float(numeric)


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
    frame["model_coverage_score"] = pd.to_numeric(frame["model_coverage_score"], errors="coerce")
    frame["model_data_quality_score"] = pd.to_numeric(frame["model_data_quality_score"], errors="coerce")
    frame["hs300_drawdown_60d"] = pd.to_numeric(frame["hs300_drawdown_60d"], errors="coerce")
    for column in [
        "avix_percentile_2y",
        "avix_zscore_1y",
        "avix_5d_change",
        "qvix_confirmation",
        "realized_vol",
        "drawdown_pressure",
        "breadth_pressure",
        "turnover_stress",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in [
        "model_missing_components",
        "breadth_mode",
        "breadth_quality",
        "breadth_source",
    ]:
        frame[column] = frame[column].fillna("").astype(str).replace("nan", "")
    quality = frame["quality"].fillna("").astype(str)
    derived_breadth = ~quality.str.contains("WARN_BREADTH_MISSING", regex=False)
    for column in ["breadth_observed", "plot_eligible"]:
        values = frame[column].map(_optional_bool)
        frame[column] = [
            bool(default) if value is None else value
            for value, default in zip(values, derived_breadth)
        ]
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
    breadth_pressure = _market_value(latest, "breadth_pressure")
    quality_text = str(latest.get("quality") or "")
    breadth_observed = (
        breadth_pressure is not None
        and "WARN_BREADTH_MISSING" not in quality_text
    )
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
        "quality": quality_text,
        "model_confidence": _confidence_score(latest),
        "model_coverage_score": _confidence_value(latest, "coverage_score"),
        "model_data_quality_score": _confidence_value(latest, "data_quality_score"),
        "model_missing_components": str(
            (latest.get("model_confidence") or {}).get("missing_components") or ""
        )
        if isinstance(latest.get("model_confidence"), dict)
        else "",
        "hs300_drawdown_60d": _market_value(latest, "hs300_drawdown_60d"),
        "breadth_mode": _market_text(latest, "breadth_mode"),
        "breadth_quality": _market_text(latest, "breadth_quality"),
        "breadth_source": _market_text(latest, "breadth_source"),
        "breadth_observed": breadth_observed,
        "plot_eligible": breadth_observed,
        "avix_percentile_2y": _component_value(latest, "avix_percentile_2y"),
        "avix_zscore_1y": _component_value(latest, "avix_zscore_1y"),
        "avix_5d_change": _component_value(latest, "avix_5d_change"),
        "qvix_confirmation": _component_value(latest, "qvix_confirmation"),
        "realized_vol": _component_value(latest, "realized_vol"),
        "drawdown_pressure": _component_value(latest, "drawdown_pressure"),
        "breadth_pressure": _component_value(latest, "breadth_pressure"),
        "turnover_stress": _component_value(latest, "turnover_stress"),
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
            "eligible_count": 0,
            "excluded_count": 0,
            "has_final": False,
            "rows": [],
            "available_dates": [],
            "core_tail_signal": core_tail_signal_payload(frame, preferred_trade_date),
            "core_tail_day_summary": _core_tail_day_summary([]),
            "methodology": _methodology(),
        }

    available_dates = sorted(frame["trade_date"].dropna().astype(str).unique().tolist())
    requested = str(preferred_trade_date or "")[:10]
    trade_date = requested if requested in available_dates else available_dates[-1]
    day = frame[frame["trade_date"].eq(trade_date)].sort_values("sampled_at")
    signal_timeline = [
        core_tail_signal_payload(day.iloc[: end + 1], trade_date)
        for end in range(len(day))
    ]
    rows = []
    for index, row in enumerate(day.itertuples(index=False)):
        tail_signal = signal_timeline[index]
        rows.append({
            "sampled_at": row.sampled_at,
            "time": str(row.sampled_at)[11:16],
            "risk_temperature": round(float(row.risk_temperature), 2),
            "temperature_mode": row.temperature_mode,
            "temperature_mode_cn": row.temperature_mode_cn,
            "is_final": bool(row.is_final),
            "quality": row.quality,
            "model_confidence": None if pd.isna(row.model_confidence) else float(row.model_confidence),
            "model_coverage_score": None
            if pd.isna(row.model_coverage_score)
            else float(row.model_coverage_score),
            "model_data_quality_score": None
            if pd.isna(row.model_data_quality_score)
            else float(row.model_data_quality_score),
            "model_missing_components": row.model_missing_components,
            "hs300_drawdown_60d": None
            if pd.isna(row.hs300_drawdown_60d)
            else float(row.hs300_drawdown_60d),
            "breadth_mode": row.breadth_mode,
            "breadth_quality": row.breadth_quality,
            "breadth_source": row.breadth_source,
            "breadth_observed": bool(row.breadth_observed),
            "plot_eligible": bool(row.plot_eligible),
            "core_tail_status": tail_signal["status"],
            "core_tail_status_cn": tail_signal["status_cn"],
            "core_tail_sample_state": tail_signal.get("latest_sample_state"),
            "core_tail_sample_state_cn": SAMPLE_STATE_CN.get(
                str(tail_signal.get("latest_sample_state") or ""),
                str(tail_signal.get("latest_sample_state") or ""),
            ),
            "core_tail_consecutive_samples": tail_signal.get("consecutive_samples", 0),
            "core_tail_stable": bool(tail_signal.get("stable")),
            "core_tail_degraded": bool(tail_signal.get("degraded")),
            "core_tail_actionable_at_sample": bool(tail_signal.get("actionable")),
            "core_tail_uncertainty": (
                (tail_signal.get("conditions") or {}).get("uncertainty")
                or tail_signal.get("degraded_uncertainty")
            ),
        })
    eligible_count = sum(item["plot_eligible"] for item in rows)
    return {
        "status": "ok",
        "trade_date": trade_date,
        "sample_count": len(rows),
        "eligible_count": eligible_count,
        "excluded_count": len(rows) - eligible_count,
        "has_final": any(item["is_final"] for item in rows),
        "first_sample_at": rows[0]["sampled_at"],
        "last_sample_at": rows[-1]["sampled_at"],
        "rows": rows,
        "available_dates": available_dates,
        "core_tail_signal": signal_timeline[-1],
        "core_tail_day_summary": _core_tail_day_summary(rows),
        "methodology": _methodology(),
    }


def _core_tail_day_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stable = next(
        (row for row in rows if row.get("core_tail_status") in {"PREPARE", "EXECUTE"}),
        None,
    )
    executed = next(
        (row for row in rows if row.get("core_tail_status") == "EXECUTE"),
        None,
    )
    return {
        "ever_stable": stable is not None,
        "stable_at": stable.get("sampled_at") if stable else None,
        "execute_triggered": executed is not None,
        "execute_at": executed.get("sampled_at") if executed else None,
        "execute_degraded": bool(executed and executed.get("core_tail_degraded")),
    }


def _tail_sample_status(row: pd.Series) -> dict[str, Any]:
    return core_tail_condition_status(
        risk_temperature=row.get("risk_temperature"),
        hs300_drawdown_60d=row.get("hs300_drawdown_60d"),
        model_confidence=row.get("model_confidence"),
        model_coverage_score=row.get("model_coverage_score"),
        model_data_quality_score=row.get("model_data_quality_score"),
        model_missing_components=row.get("model_missing_components"),
        breadth_mode=row.get("breadth_mode"),
        breadth_quality=row.get("breadth_quality"),
        temperature_mode=row.get("temperature_mode"),
        component_scores={
            "avix_percentile_2y": row.get("avix_percentile_2y"),
            "avix_zscore_1y": row.get("avix_zscore_1y"),
            "avix_5d_change": row.get("avix_5d_change"),
            "qvix_confirmation": row.get("qvix_confirmation"),
            "realized_vol": row.get("realized_vol"),
            "drawdown_pressure": row.get("drawdown_pressure"),
            "breadth_pressure": row.get("breadth_pressure"),
            "turnover_stress": row.get("turnover_stress"),
        },
    )


def core_tail_signal_payload(
    history: pd.DataFrame | None,
    preferred_trade_date: str | None = None,
) -> dict[str, Any]:
    frame = _normalized_history(history)
    policy = core_tail_policy_payload()
    empty = {
        "status": "NO_SAMPLE",
        "status_cn": "等待盘中采样",
        "trade_date": str(preferred_trade_date or "")[:10] or None,
        "candidate": False,
        "stable": False,
        "actionable": False,
        "consecutive_samples": 0,
        "stability_span_minutes": 0,
        "conditions": {},
        "policy": policy,
    }
    if frame.empty:
        return empty

    available_dates = sorted(frame["trade_date"].dropna().astype(str).unique().tolist())
    requested = str(preferred_trade_date or "")[:10]
    trade_date = requested if requested in available_dates else available_dates[-1]
    day = frame[frame["trade_date"].eq(trade_date)].sort_values("sampled_at").copy()
    if day.empty:
        return {**empty, "trade_date": trade_date}

    day["sample_ts"] = day["sampled_at"].map(_shanghai_timestamp)
    day = day[day["sample_ts"].notna()].copy()
    if day.empty:
        return {**empty, "trade_date": trade_date}

    latest = day.iloc[-1]
    latest_status = _tail_sample_status(latest)
    latest_is_final = bool(latest.get("is_final"))
    latest_state = str(latest_status["state"])
    candidate = latest_state in CORE_TAIL_PASS_STATES and not latest_is_final
    trailing: list[pd.Series] = []
    invalid_samples_skipped = 0
    next_timestamp: pd.Timestamp | None = None
    for _, row in day.iloc[::-1].iterrows():
        status = _tail_sample_status(row)
        timestamp = row["sample_ts"]
        if bool(row.get("is_final")):
            break
        if status["state"] in CORE_TAIL_SKIP_STATES:
            invalid_samples_skipped += 1
            continue
        if status["state"] in CORE_TAIL_FAIL_STATES:
            break
        gap_ok = next_timestamp is None or (next_timestamp - timestamp).total_seconds() <= (
            CORE_TAIL_MAX_SAMPLE_GAP_MINUTES * 60
        )
        if not gap_ok:
            break
        trailing.append(row)
        next_timestamp = timestamp

    trailing.reverse()
    if trailing and latest["sample_ts"] - trailing[-1]["sample_ts"] > pd.Timedelta(
        minutes=CORE_TAIL_MAX_SAMPLE_GAP_MINUTES
    ):
        trailing = []
    span_minutes = 0
    if len(trailing) >= 2:
        span_minutes = int(
            (trailing[-1]["sample_ts"] - trailing[0]["sample_ts"]).total_seconds() // 60
        )
    stable = len(trailing) >= CORE_TAIL_STABLE_SAMPLES and span_minutes >= CORE_TAIL_STABLE_MINUTES
    trailing_statuses = [_tail_sample_status(row) for row in trailing]
    degraded_statuses = [
        item for item in trailing_statuses if item["state"] == "DEGRADED_PASS"
    ]
    degraded_samples = len(degraded_statuses)
    degraded_uncertainty = (
        degraded_statuses[-1].get("uncertainty") if degraded_statuses else None
    )

    sampled_at = latest["sample_ts"]
    minute = sampled_at.strftime("%H:%M")
    if latest_is_final:
        status, status_cn = "FINAL", "已转正式收盘，尾盘窗口结束"
    elif latest_state == "INDETERMINATE":
        bounded = latest_status.get("uncertainty") or {}
        lower = bounded.get("risk_temperature_lower")
        upper = bounded.get("risk_temperature_upper")
        range_cn = (
            f"RT可能区间 {float(lower):.1f}-{float(upper):.1f}"
            if lower is not None and upper is not None
            else "风险边界无法确定"
        )
        status, status_cn = "DATA_WAIT", f"缺失数据导致条件不确定，已跳过（{range_cn}）"
    elif latest_state == "INVALID":
        reasons = "/".join(
            INVALID_REASON_CN.get(reason, reason) for reason in latest_status["invalid_reasons"]
        ) or "原因未知"
        status, status_cn = "DATA_WAIT", f"本次数据无法量化，已跳过（{reasons}）"
    elif latest_state in CORE_TAIL_FAIL_STATES:
        status, status_cn = (
            ("INACTIVE", "缺失数据取极端范围后仍不满足买入条件")
            if latest_state == "DEGRADED_FAIL"
            else ("INACTIVE", "严格条件未同时满足")
        )
    elif not stable:
        prefix = "降级稳健通过，" if latest_state == "DEGRADED_PASS" else ""
        status, status_cn = (
            "CONFIRMING",
            f"{prefix}正在确认稳定性 {len(trailing)}/{CORE_TAIL_STABLE_SAMPLES}",
        )
    elif minute < CORE_TAIL_WINDOW_START:
        prefix = "降级稳健信号" if degraded_samples else "信号"
        status, status_cn = "PREPARE", f"{prefix}已稳定，{CORE_TAIL_WINDOW_START} 准备买入"
    elif minute < CORE_TAIL_WINDOW_END:
        prefix = "降级稳健" if degraded_samples else ""
        status, status_cn = "EXECUTE", f"{prefix}尾盘执行窗口：买入核心仓"
    else:
        status, status_cn = "WINDOW_CLOSED", "尾盘窗口已过，回退 T+1 开盘"

    valid_until = sampled_at + pd.Timedelta(minutes=CORE_TAIL_SAMPLE_TTL_MINUTES)
    if status == "EXECUTE":
        window_end = sampled_at.normalize() + pd.Timedelta(hours=15)
        valid_until = min(valid_until, window_end)
    return {
        "status": status,
        "status_cn": status_cn,
        "trade_date": trade_date,
        "candidate": candidate,
        "stable": stable,
        "actionable": status == "EXECUTE",
        "consecutive_samples": len(trailing),
        "stability_span_minutes": span_minutes,
        "invalid_samples_skipped": invalid_samples_skipped,
        "degraded_samples_in_streak": degraded_samples,
        "degraded": degraded_samples > 0,
        "degraded_uncertainty": degraded_uncertainty,
        "latest_sample_state": latest_state,
        "latest_sample_state_cn": SAMPLE_STATE_CN.get(latest_state, latest_state),
        "last_sample_at": sampled_at.isoformat(timespec="seconds"),
        "valid_until": valid_until.isoformat(timespec="seconds"),
        "conditions": {
            **latest_status,
            "model_coverage_score": None
            if pd.isna(latest.get("model_coverage_score"))
            else float(latest.get("model_coverage_score")),
            "model_data_quality_score": None
            if pd.isna(latest.get("model_data_quality_score"))
            else float(latest.get("model_data_quality_score")),
            "model_missing_components": str(latest.get("model_missing_components") or ""),
            "breadth_mode": str(latest.get("breadth_mode") or ""),
            "breadth_quality": str(latest.get("breadth_quality") or ""),
            "breadth_source": str(latest.get("breadth_source") or ""),
        },
        "policy": policy,
        "execution_cn": "完整通过或缺失因子全范围下仍稳健通过时，仅沪深300核心仓在 T 日 14:50-15:00 买入；卫星及其他信号仍为 T+1 开盘",
        "fallback_cn": "未稳定、数据过期或错过尾盘窗口时，不追单，回退 T+1 开盘",
    }


def confirmed_core_tail_dates(history: pd.DataFrame | None) -> set[str]:
    """Return dates where the recorded samples actually opened the tail window."""
    frame = _normalized_history(history)
    confirmed: set[str] = set()
    for trade_date in sorted(frame["trade_date"].dropna().astype(str).unique()):
        day = frame[frame["trade_date"].eq(trade_date)].sort_values("sampled_at")
        for end in range(1, len(day) + 1):
            signal = core_tail_signal_payload(day.iloc[:end], trade_date)
            if signal["status"] == "EXECUTE" and signal["actionable"]:
                confirmed.add(trade_date)
                break
    return confirmed


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
        "chart_quality": "Samples with missing A-share breadth remain in the audit history but are excluded from the connected trend line.",
        "signal_states": "PASS/DEGRADED_PASS increment; FAIL/DEGRADED_FAIL reset; INDETERMINATE/INVALID are audited and skipped without resetting.",
    }
