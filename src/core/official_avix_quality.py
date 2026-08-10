from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from src.utils.config import load_thresholds


TARGET_DTE = 30
MIN_OPTIONS_PER_TERM = int(load_thresholds()["min_options_per_term"])


@dataclass(frozen=True)
class OfficialAvixAssessment:
    ready: bool
    reasons: tuple[str, ...]


def _number(row: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        value = pd.to_numeric(row.get(name), errors="coerce")
        if pd.notna(value):
            return float(value)
    return None


def assess_official_avix(row: Mapping[str, Any]) -> OfficialAvixAssessment:
    """Decide whether an AVIX row is structurally fit for official publication."""
    reasons: list[str] = []
    avix = _number(row, "avix_clean", "avix_raw", "avix")
    if avix is None or avix <= 0:
        reasons.append("MISSING_OR_INVALID_VALUE")

    quality = str(row.get("quality", "") or "")
    flags = {flag.strip() for flag in quality.replace(",", "|").split("|") if flag.strip()}
    if any(flag.startswith(("BAD", "LOW")) for flag in flags):
        reasons.append("UNUSABLE_QUALITY")
    if "WARN_NOT_BRACKET_30D" in flags:
        reasons.append("NOT_BRACKET_30D")
    if "WARN_FEW_OPTIONS" in flags:
        reasons.append("SPARSE_TERM")

    near_dte = _number(row, "near_dte")
    next_dte = _number(row, "next_dte")
    near_expiry = str(row.get("near_expiry", "") or "")
    next_expiry = str(row.get("next_expiry", "") or "")
    if near_dte is None or next_dte is None or not near_expiry or not next_expiry:
        reasons.append("MISSING_TERM_METADATA")
    else:
        exact_target = (
            near_expiry == next_expiry
            and near_dte == TARGET_DTE
            and next_dte == TARGET_DTE
        )
        bracketed_target = (
            near_expiry != next_expiry
            and near_dte < TARGET_DTE
            and next_dte > TARGET_DTE
        )
        if not (exact_target or bracketed_target):
            reasons.append("TERM_STRUCTURE_NOT_30D")

    near_count = _number(row, "near_n_options")
    next_count = _number(row, "next_n_options")
    if (
        near_count is None
        or next_count is None
        or near_count < MIN_OPTIONS_PER_TERM
        or next_count < MIN_OPTIONS_PER_TERM
    ):
        reasons.append("INSUFFICIENT_OPTIONS_PER_TERM")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return OfficialAvixAssessment(ready=not unique_reasons, reasons=unique_reasons)


def official_avix_ready(row: Mapping[str, Any]) -> bool:
    return assess_official_avix(row).ready
