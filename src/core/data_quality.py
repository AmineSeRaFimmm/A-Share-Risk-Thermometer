from __future__ import annotations

from datetime import datetime
import math

import pandas as pd


QUALITY_FIELDS = [
    "source_quote_time",
    "fetch_time",
    "age_seconds",
    "is_proxy",
    "is_delayed",
    "sample_size",
    "observed",
    "quality_flags",
]


def _timestamp(value, *, trade_date: str | None = None) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if trade_date and len(text) <= 8 and ":" in text:
        text = f"{str(trade_date)[:10]}T{text}"
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("Asia/Shanghai")
    else:
        parsed = parsed.tz_convert("Asia/Shanghai")
    return parsed


def quality_metadata(
    *,
    source: str,
    trade_date: str,
    source_quote_time=None,
    fetch_time=None,
    sample_size: int | None = None,
    is_proxy: bool = False,
    is_delayed: bool = False,
    observed: bool = True,
    max_age_seconds: int | None = None,
    now=None,
    flags: list[str] | None = None,
) -> dict[str, object]:
    fetched = _timestamp(fetch_time) or pd.Timestamp.now(tz="Asia/Shanghai")
    quote = _timestamp(source_quote_time, trade_date=trade_date)
    current = _timestamp(now) or pd.Timestamp.now(tz="Asia/Shanghai")
    age_seconds = None if quote is None else max(0, int((current - quote).total_seconds()))

    quality_flags = list(flags or [])
    if not observed:
        quality_flags.append("MISSING")
    if quote is None:
        quality_flags.append("TIME_UNVERIFIED")
    elif max_age_seconds is not None and age_seconds is not None and age_seconds > max_age_seconds:
        quality_flags.append("STALE")
    if is_proxy:
        quality_flags.append("PROXY")
    if is_delayed:
        quality_flags.append("DELAYED")
    if sample_size is not None and sample_size <= 0:
        quality_flags.append("EMPTY_SAMPLE")

    unique_flags = sorted(set(flag for flag in quality_flags if flag))
    return {
        "source": str(source or ""),
        "source_quote_time": quote.isoformat(timespec="seconds") if quote is not None else None,
        "fetch_time": fetched.isoformat(timespec="seconds"),
        "age_seconds": age_seconds,
        "is_proxy": bool(is_proxy),
        "is_delayed": bool(is_delayed),
        "sample_size": int(sample_size) if sample_size is not None else None,
        "observed": bool(observed),
        "quality_flags": "|".join(unique_flags) if unique_flags else "OK",
    }


def observation_quality_score(
    *,
    observed: bool,
    quality_flags: str | None = None,
    age_seconds=None,
    max_age_seconds: int | None = None,
    source_agreement: float | None = None,
) -> float:
    if not observed:
        return 0.0
    score = 1.0
    flags = set(str(quality_flags or "").split("|"))
    if "PROXY" in flags:
        score *= 0.60
    if "DELAYED" in flags:
        score *= 0.80
    if "TIME_UNVERIFIED" in flags:
        score *= 0.85
    if "STALE" in flags:
        score *= 0.50
    if "SPARSE" in flags or "EMPTY_SAMPLE" in flags:
        score *= 0.50
    try:
        age = float(age_seconds)
    except (TypeError, ValueError):
        age = math.nan
    if max_age_seconds and math.isfinite(age) and age > 0:
        score *= max(0.25, min(1.0, max_age_seconds / age))
    if source_agreement is not None:
        score *= max(0.0, min(1.0, float(source_agreement)))
    return round(max(0.0, min(1.0, score)), 4)


def fetch_time_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
