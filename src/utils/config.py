from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.storage.paths import ROOT

CONFIG_DIR = ROOT / "config"

DEFAULT_WEIGHTS = {
    "avix_percentile_2y": 0.28,
    "avix_zscore_1y": 0.14,
    "avix_5d_change": 0.08,
    "qvix_confirmation": 0.12,
    "realized_vol_percentile": 0.12,
    "drawdown_pressure": 0.12,
    "market_breadth_pressure": 0.10,
    "turnover_stress": 0.04,
}

# (upper_exclusive, code, cn_label); last band uses upper 101 so temp < 101 covers 100
DEFAULT_REGIMES = [
    (20, "CALM", "平静"),
    (40, "NORMAL", "正常"),
    (60, "CAUTION", "警戒"),
    (75, "HIGH_RISK", "高风险"),
    (90, "PANIC", "恐慌区"),
    (101, "EXTREME_PANIC", "极端恐慌"),
]


@lru_cache(maxsize=16)
def load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def clear_config_cache() -> None:
    load_yaml.cache_clear()


def load_weights() -> dict[str, float]:
    scoring = load_yaml("scoring.yml")
    raw = scoring.get("weights") or DEFAULT_WEIGHTS
    weights = {str(k): float(v) for k, v in raw.items()}
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"scoring.yml weights must sum to 1.0, got {total}")
    for key in DEFAULT_WEIGHTS:
        if key not in weights:
            raise ValueError(f"scoring.yml missing weight key: {key}")
    return weights


def load_regimes() -> list[tuple[float, str, str]]:
    """Build REGIMES list from scoring.yml temperature_zones when present."""
    scoring = load_yaml("scoring.yml")
    zones = scoring.get("temperature_zones")
    if not zones:
        return list(DEFAULT_REGIMES)
    # Map zone keys to codes/labels used in the app
    zone_meta = [
        ("calm", "CALM", "平静"),
        ("normal", "NORMAL", "正常"),
        ("caution", "CAUTION", "警戒"),
        ("high_risk", "HIGH_RISK", "高风险"),
        ("panic", "PANIC", "恐慌区"),
        ("extreme_panic", "EXTREME_PANIC", "极端恐慌"),
    ]
    regimes: list[tuple[float, str, str]] = []
    for key, code, cn in zone_meta:
        bounds = zones.get(key)
        if not bounds or len(bounds) != 2:
            return list(DEFAULT_REGIMES)
        upper = float(bounds[1])
        # extreme_panic upper is inclusive 100; use 101 so temp < 101 covers 100
        if key == "extreme_panic" and upper <= 100:
            upper = 101.0
        regimes.append((upper, code, cn))
    return regimes


def load_thresholds() -> dict[str, Any]:
    thresholds = load_yaml("thresholds.yml")
    quality = thresholds.get("quality") or {}
    avix = thresholds.get("avix") or {}
    breadth = thresholds.get("breadth") or {}
    return {
        "min_options_per_term": int(quality.get("min_options_per_term", 8)),
        "preferred_options_per_term": int(quality.get("preferred_options_per_term", 12)),
        "max_raw_clean_diff": float(quality.get("max_raw_clean_diff", 2.0)),
        "min_qvix_corr_60": float(quality.get("min_qvix_corr_60", 0.60)),
        "min_history_days_for_percentile": int(quality.get("min_history_days_for_percentile", 120)),
        "fixed_warning_level": float(avix.get("fixed_warning_level", 22)),
        "fixed_panic_level": float(avix.get("fixed_panic_level", 25)),
        "percentile_warning": float(avix.get("percentile_warning", 0.80)),
        "percentile_panic": float(avix.get("percentile_panic", 0.90)),
        "weak_advancing_ratio": float(breadth.get("weak_advancing_ratio", 0.35)),
        "panic_advancing_ratio": float(breadth.get("panic_advancing_ratio", 0.20)),
        "extreme_down_ratio": float(breadth.get("extreme_down_ratio", 0.08)),
    }


def load_data_sources() -> dict[str, Any]:
    cfg = load_yaml("data_sources.yml")
    akshare = cfg.get("akshare") or {}
    return {
        "retry_times": int(akshare.get("retry_times", 3)),
        "retry_sleep_seconds": float(akshare.get("retry_sleep_seconds", 5)),
        "request_gap_seconds": float(akshare.get("request_gap_seconds", 0.5)),
        "storage": cfg.get("storage") or {},
        "site": cfg.get("site") or {},
    }


def config_path(name: str) -> Path:
    return CONFIG_DIR / name
