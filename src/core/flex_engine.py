"""Combined Flex portfolio engine: state machine, sizing, multi-stage merge.

Research playbook only — not investment advice.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.core.sector_etf_map import attach_etf_fields, map_csi300, map_sector
from src.storage.paths import CALCULATED

# ---------------------------------------------------------------------------
# Constants aligned with research/backtest_core_plus_sectors.py
# ---------------------------------------------------------------------------

CORE_HOLD_DAYS = 5
SAT_MIN_HOLD = 3
SAT_MAX_HOLD = 8
SAT_DEFAULT_HOLD = 5

# Conservative (default) vs aggressive Flex
MODE_CONSERVATIVE = "conservative"
MODE_AGGRESSIVE = "aggressive"

SIZING = {
    MODE_CONSERVATIVE: {
        "core_when_signal": 0.50,
        "sat_when_signal": 0.30,
        "total_cap": 0.80,
        "flex_single_full": False,
        "label_cn": "保守：核心≤50% / 卫星≤30% / 总暴露≤80%",
    },
    MODE_AGGRESSIVE: {
        "core_when_signal": 0.60,
        "sat_when_signal": 0.40,
        "total_cap": 1.00,
        "flex_single_full": True,  # only one sleeve → 100%
        "label_cn": "进取 Flex：单仓100%；双仓 60/40",
    },
}

QUALITY_WEIGHT = {
    "good": 1.0,
    "proxy": 0.70,
    "weak": 0.0,  # excluded from default buy basket
    "missing": 0.0,
}

# ETF-realistic haircut on sector-index proxy returns (stress)
QUALITY_RETURN_HAIRCUT = {
    "good": 1.00,
    "proxy": 0.85,
    "weak": 0.60,
    "missing": 0.50,
}

# Stage confidence: high = full sat; observe = tiny; excluded from default flex open
STAGE_TIER = {
    "CSI300_CORE_BUY": "high",
    "RISING_HARD": "high",
    "FALLING_HARD": "high",
    "ENTER_70_BOUNCE": "observe",  # small n
    "HIGH_COOLING": "observe",  # tiny trade count in combo BT
    "PANIC_SMALL_N": "observe",
    "CALM": "excluded",  # over-trades in combo BT
}

STAGE_TIER_WEIGHT = {
    "high": 1.0,
    "observe": 0.25,
    "excluded": 0.0,
}

MIN_N_FULL = 100  # sector needs n>=100 for full weight in high tier
MIN_N_OBSERVE = 25

# On RISING_HARD same day, do not chase these for same-day entry (event study)
RISING_SAME_DAY_AVOID_OPEN = {"恒生科技", "电子", "计算机"}

FLEX_SAT_LONG: dict[str, list[str]] = {
    "RISING_HARD": ["通信", "电子", "机械设备", "国防军工"],
    "CSI300_CORE_BUY": ["建筑材料", "商贸零售", "传媒", "恒生科技"],
    "ENTER_70_BOUNCE": ["恒生科技"],
    "HIGH_COOLING": ["石油石化", "综合", "轻工制造", "煤炭", "传媒"],
    "FALLING_HARD": ["有色金属", "煤炭", "基础化工", "电力设备"],
}

FLEX_SAT_SHORT: dict[str, list[str]] = {
    "RISING_HARD": ["美容护理", "房地产", "钢铁"],
    "CSI300_CORE_BUY": ["公用事业", "银行"],
    "ENTER_70_BOUNCE": ["石油石化", "公用事业", "银行"],
    "HIGH_COOLING": ["电子", "计算机"],
    "FALLING_HARD": ["计算机", "传媒", "美容护理"],
}

# Score seed per stage (higher = preferred when merging)
STAGE_MERGE_SCORE = {
    "CSI300_CORE_BUY": 1.0,
    "HIGH_COOLING": 0.85,
    "ENTER_70_BOUNCE": 0.80,
    "RISING_HARD": 0.75,
    "FALLING_HARD": 0.70,
    "PANIC_SMALL_N": 0.40,
    "CALM": 0.20,
}

# Opposite regimes for event-exit of satellite
STAGE_OPPOSITES = {
    "RISING_HARD": {"FALLING_HARD", "HIGH_COOLING"},
    "FALLING_HARD": {"RISING_HARD"},
    "CSI300_CORE_BUY": set(),  # hold fixed schedule for core-linked sat unless max hold
    "ENTER_70_BOUNCE": {"FALLING_HARD", "HIGH_COOLING"},
    "HIGH_COOLING": {"RISING_HARD"},
}

# Sector meta for ranking (from event study; frozen research snapshot)
SECTOR_META: dict[str, dict[str, Any]] = {
    "通信": {"win_rate": 0.59, "n": 438, "mean_excess": 0.0079},
    "电子": {"win_rate": 0.55, "n": 438, "mean_excess": 0.0066},
    "机械设备": {"win_rate": 0.57, "n": 438, "mean_excess": 0.0045},
    "国防军工": {"win_rate": 0.55, "n": 438, "mean_excess": 0.0040},
    "建筑材料": {"win_rate": 0.58, "n": 184, "mean_excess": 0.0050},
    "商贸零售": {"win_rate": 0.59, "n": 184, "mean_excess": 0.0050},
    "传媒": {"win_rate": 0.58, "n": 184, "mean_excess": 0.0050},
    "恒生科技": {"win_rate": 0.58, "n": 165, "mean_excess": 0.0080},
    "石油石化": {"win_rate": 0.76, "n": 37, "mean_excess": 0.0149},
    "综合": {"win_rate": 0.69, "n": 39, "mean_excess": 0.0160},
    "轻工制造": {"win_rate": 0.69, "n": 39, "mean_excess": 0.0150},
    "煤炭": {"win_rate": 0.59, "n": 37, "mean_excess": 0.0150},
    "有色金属": {"win_rate": 0.53, "n": 477, "mean_excess": 0.0063},
    "基础化工": {"win_rate": 0.57, "n": 477, "mean_excess": 0.0050},
    "电力设备": {"win_rate": 0.53, "n": 477, "mean_excess": 0.0045},
    "公用事业": {"win_rate": 0.44, "n": 184, "mean_excess": -0.003},
    "银行": {"win_rate": 0.49, "n": 184, "mean_excess": -0.001},
    "美容护理": {"win_rate": 0.40, "n": 327, "mean_excess": -0.004},
    "房地产": {"win_rate": 0.42, "n": 438, "mean_excess": -0.003},
    "钢铁": {"win_rate": 0.42, "n": 438, "mean_excess": -0.003},
    "计算机": {"win_rate": 0.44, "n": 477, "mean_excess": -0.002},
}

# Rough beta for risk dashboard (research heuristics)
SECTOR_BETA = {
    "沪深300": 1.0,
    "银行": 0.85,
    "公用事业": 0.75,
    "煤炭": 1.05,
    "有色金属": 1.15,
    "石油石化": 1.00,
    "建筑材料": 1.10,
    "商贸零售": 1.05,
    "传媒": 1.20,
    "恒生科技": 1.35,
    "通信": 1.20,
    "电子": 1.30,
    "机械设备": 1.15,
    "国防军工": 1.25,
    "计算机": 1.25,
    "电力设备": 1.25,
    "基础化工": 1.10,
    "综合": 1.00,
    "轻工制造": 1.05,
    "美容护理": 1.00,
    "房地产": 1.10,
    "钢铁": 1.10,
}

POSITION_STATE_PATH = CALCULATED / "flex_position_state.json"

# Cost-stress snapshot (filled/updated by backtest; defaults from last research)
DEFAULT_BACKTEST_STATS: dict[str, Any] = {
    "mode": "combined_flex_v2",
    "label_cn": "组合 Flex v2（状态机+质量降权+成本压力）",
    "default_mode": MODE_AGGRESSIVE,
    "hold_days_core": CORE_HOLD_DAYS,
    "hold_days_sat": f"{SAT_MIN_HOLD}-{SAT_MAX_HOLD}",
    "execution": "T 收盘信号 → T+1 开盘",
    "core_only": {
        "total_return": 0.8523,
        "ann_return": 0.1030,
        "max_dd": -0.1065,
        "win_rate": 0.6531,
        "trade_count": 49,
    },
    "conservative": {
        "note": "默认推荐；总暴露 capped",
        "full_sample": {},
        "oos": {},
    },
    "aggressive": {
        "note": "生产进取模式；单仓满仓、双仓60/40；日度路径与换仓成本口径",
        "full_sample": {
            "total_return": 6.1101,
            "ann_return": 0.3660,
            "max_dd": -0.1391,
            "win_rate": 0.6285,
            "trade_count": 253,
        },
        "oos": {
            "total_return": 2.0078,
            "ann_return": 0.5772,
            "max_dd": -0.1275,
            "win_rate": 0.6373,
            "trade_count": 102,
        },
    },
    "cost_stress": {
        "base_bps_one_way": 1,
        "stress_15bps": {},
        "stress_30bps": {},
        "etf_haircut_note": "proxy 正收益折扣、负收益放大；weak 剔除；行业指数≠ETF",
    },
    "caveat_cn": "板块用行业指数代理；弱代理不进默认篮子；实盘收益应低于回测。",
}


@dataclass
class SleevePos:
    status: str = "flat"  # flat | open
    entry_signal_date: str | None = None
    entry_date: str | None = None
    exit_due_date: str | None = None
    days_held: int = 0
    days_remaining: int = 0
    stage_id: str | None = None
    names: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    etf_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FlexState:
    as_of: str | None = None
    mode: str = MODE_CONSERVATIVE
    core: SleevePos = field(default_factory=SleevePos)
    satellite: SleevePos = field(default_factory=SleevePos)
    last_actions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "mode": self.mode,
            "core": self.core.to_dict(),
            "satellite": self.satellite.to_dict(),
            "last_actions": self.last_actions,
        }


def load_position_state(path: Path | None = None) -> FlexState:
    p = path or POSITION_STATE_PATH
    if not p.exists():
        return FlexState()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return FlexState()
    st = FlexState(as_of=raw.get("as_of"), mode=raw.get("mode") or MODE_CONSERVATIVE)
    for key, attr in (("core", "core"), ("satellite", "satellite")):
        d = raw.get(key) or {}
        setattr(
            st,
            attr,
            SleevePos(
                status=d.get("status") or "flat",
                entry_signal_date=d.get("entry_signal_date"),
                entry_date=d.get("entry_date"),
                exit_due_date=d.get("exit_due_date"),
                days_held=int(d.get("days_held") or 0),
                days_remaining=int(d.get("days_remaining") or 0),
                stage_id=d.get("stage_id"),
                names=list(d.get("names") or []),
                weights=dict(d.get("weights") or {}),
                etf_code=d.get("etf_code"),
            ),
        )
    st.last_actions = list(raw.get("last_actions") or [])
    return st


def save_position_state(state: FlexState, path: Path | None = None) -> None:
    p = path or POSITION_STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _quality_of(name: str) -> str:
    m = map_sector(name)
    return str(m.get("quality") or "missing")


def quality_adjusted_return(r: float, quality: str) -> float:
    """Apply ETF proxy realism without making losses look smaller."""
    q = str(quality or "missing")
    h = float(QUALITY_RETURN_HAIRCUT.get(q, 0.85))
    if h <= 0:
        return float(r)
    if r >= 0:
        return float(r) * h
    return float(r) / h


def _sector_score(name: str, stage_id: str) -> float:
    meta = SECTOR_META.get(name) or {}
    n = float(meta.get("n") or 30)
    wr = float(meta.get("win_rate") or 0.5)
    me = float(meta.get("mean_excess") or 0.003)
    q = QUALITY_WEIGHT.get(_quality_of(name), 0.0)
    tier = STAGE_TIER.get(stage_id, "excluded")
    tw = STAGE_TIER_WEIGHT.get(tier, 0.0)
    if q <= 0 or tw <= 0:
        return 0.0
    if tier == "high" and n < MIN_N_FULL:
        tw *= 0.5
    if n < MIN_N_OBSERVE:
        return 0.0
    # score = mean_excess * sqrt(n) * win_rate * quality * tier
    return max(0.0, me) * math.sqrt(max(n, 1.0)) * wr * q * tw * STAGE_MERGE_SCORE.get(stage_id, 0.5)


def merge_satellite_targets(
    stages: list[str],
    *,
    rising_hard: bool = False,
    include_observe: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Merge multi-stage long/short with scores. Returns (longs, avoids, suppressed_stages)."""
    active_sat = [s for s in stages if STAGE_TIER.get(s, "excluded") != "excluded"]
    if not include_observe:
        active_sat = [s for s in active_sat if STAGE_TIER.get(s) == "high"]

    score_map: dict[str, float] = {}
    stage_src: dict[str, list[str]] = {}
    why_map: dict[str, str] = {}
    meta_map: dict[str, dict] = {}
    stage_score_map: dict[str, list[dict[str, Any]]] = {}
    suppressed: list[str] = []

    for sid in stages:
        if sid in FLEX_SAT_LONG and sid not in active_sat:
            suppressed.append(sid)

    for sid in active_sat:
        for name in FLEX_SAT_LONG.get(sid, []):
            # 升温当日不从 RISING 路径追 恒科/电子/计算机；CORE 名单可另计
            if rising_hard and name in RISING_SAME_DAY_AVOID_OPEN and sid == "RISING_HARD":
                continue
            sc = _sector_score(name, sid)
            if sc <= 0:
                continue
            score_map[name] = score_map.get(name, 0.0) + sc
            stage_src.setdefault(name, []).append(sid)
            meta = SECTOR_META.get(name) or {}
            meta_map[name] = meta
            stage_score_map.setdefault(name, []).append(
                {
                    "stage_id": sid,
                    "score": round(sc, 6),
                    "win_rate": meta.get("win_rate"),
                    "n": meta.get("n"),
                    "mean_excess": meta.get("mean_excess"),
                }
            )
            why_map[name] = f"阶段{'+'.join(stage_src[name])} 合并得分"

    # normalize weights among positive scores
    total = sum(score_map.values()) or 1.0
    longs: list[dict[str, Any]] = []
    for name, sc in sorted(score_map.items(), key=lambda x: -x[1]):
        q = _quality_of(name)
        meta = meta_map.get(name) or {}
        w_raw = sc / total
        longs.append(
            {
                "name": name,
                "score": round(sc, 6),
                "weight_in_sat": round(w_raw, 4),
                "quality": q,
                "quality_weight": QUALITY_WEIGHT.get(q, 0.0),
                "win_rate": meta.get("win_rate"),
                "n": meta.get("n"),
                "mean_excess": meta.get("mean_excess"),
                "stages": stage_src.get(name, []),
                "stage_evidence": stage_score_map.get(name, []),
                "why": why_map.get(name, "阶段超配"),
                "tier": STAGE_TIER.get(stage_src.get(name, [""])[0], "high"),
            }
        )

    # AVOID list from active stages (not SELL)
    avoid_names: dict[str, dict] = {}
    for sid in active_sat:
        for name in FLEX_SAT_SHORT.get(sid, []):
            meta = SECTOR_META.get(name) or {}
            avoid_names[name] = {
                "name": name,
                "side": "AVOID",
                "side_cn": "回避（若持有则减配）",
                "stages": [sid],
                "why": f"{sid} 阶段相对偏弱",
                "win_rate": meta.get("win_rate"),
                "n": meta.get("n"),
                "conditional": True,
                "condition_cn": f"仅当已持有「{name}」映射 ETF 时减至 0；无持仓则无需操作",
            }

    return longs, list(avoid_names.values()), suppressed


def compute_allocation(
    core_active: bool,
    sat_active: bool,
    mode: str = MODE_CONSERVATIVE,
) -> dict[str, Any]:
    cfg = SIZING.get(mode) or SIZING[MODE_CONSERVATIVE]
    w_core = cfg["core_when_signal"] if core_active else 0.0
    w_sat = cfg["sat_when_signal"] if sat_active else 0.0

    if cfg.get("flex_single_full"):
        if core_active and not sat_active:
            w_core, w_sat = 1.0, 0.0
        elif sat_active and not core_active:
            w_core, w_sat = 0.0, 1.0
        elif core_active and sat_active:
            w_core, w_sat = cfg["core_when_signal"], cfg["sat_when_signal"]

    total = w_core + w_sat
    cap = float(cfg["total_cap"])
    if total > cap and total > 0:
        scale = cap / total
        w_core *= scale
        w_sat *= scale
        total = cap

    if core_active and sat_active:
        alloc_mode = "BOTH"
        alloc_cn = f"双仓：核心 {w_core:.0%} + 卫星 {w_sat:.0%}（{cfg['label_cn']}）"
    elif core_active:
        alloc_mode = "CORE_ONLY"
        alloc_cn = f"仅核心：{w_core:.0%} 沪深300（{cfg['label_cn']}）"
    elif sat_active:
        alloc_mode = "SAT_ONLY"
        alloc_cn = f"仅卫星：{w_sat:.0%} 阶段板块（{cfg['label_cn']}）"
    else:
        alloc_mode = "FLAT"
        alloc_cn = "空仓观望"

    return {
        "mode": mode,
        "mode_cn": cfg["label_cn"],
        "allocation_mode": alloc_mode,
        "allocation_cn": alloc_cn,
        "w_core": round(w_core, 4),
        "w_sat": round(w_sat, 4),
        "w_cash": round(max(0.0, 1.0 - w_core - w_sat), 4),
        "total_exposure": round(w_core + w_sat, 4),
    }


def _trading_dates_from_risk(risk: pd.DataFrame) -> list[str]:
    df = risk.copy().sort_values("trade_date")
    return [str(x)[:10] for x in df["trade_date"].tolist()]


def _precompute_feature_rows(
    risk_components: pd.DataFrame,
    index_history: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """Vectorized-ish feature panel for walk-forward simulation (O(n))."""
    from src.core.stage_trade_playbook import _dd60_from_index

    df = risk_components.copy().sort_values("trade_date").reset_index(drop=True)
    df["risk_temperature"] = pd.to_numeric(df["risk_temperature"], errors="coerce")
    df = df.dropna(subset=["risk_temperature"]).reset_index(drop=True)
    rt = df["risk_temperature"].astype(float)
    prev = rt.shift(1)
    d1 = rt - prev
    d5 = rt - rt.shift(5)
    roll = rt.rolling(10, min_periods=5).max()

    if "sh000300_dd60" in df.columns:
        dd_series = pd.to_numeric(df["sh000300_dd60"], errors="coerce")
    else:
        dd_series = pd.Series([np.nan] * len(df))
    # fill last known dd from index if mostly missing
    last_dd = _dd60_from_index(index_history) if index_history is not None else None

    rows: list[dict[str, Any]] = []
    for i in range(len(df)):
        dd = dd_series.iloc[i]
        if pd.isna(dd):
            dd = last_dd
        rows.append(
            {
                "trade_date": str(df.iloc[i]["trade_date"])[:10],
                "rt": float(rt.iloc[i]),
                "prev_rt": float(prev.iloc[i]) if pd.notna(prev.iloc[i]) else float(rt.iloc[i]),
                "rt_d1": float(d1.iloc[i]) if pd.notna(d1.iloc[i]) else 0.0,
                "rt_d5": float(d5.iloc[i]) if pd.notna(d5.iloc[i]) else None,
                "rt_rollmax_10": float(roll.iloc[i]) if pd.notna(roll.iloc[i]) else float(rt.iloc[i]),
                "hs300_dd60": None if dd is None or (isinstance(dd, float) and math.isnan(dd)) else float(dd),
            }
        )
    return rows


def simulate_positions(
    risk_components: pd.DataFrame,
    index_history: pd.DataFrame | None,
    *,
    mode: str = MODE_CONSERVATIVE,
    classify_fn=None,
    active_stages_fn=None,
) -> FlexState:
    """Walk-forward simulate Flex positions to as_of so OPEN/HOLD/CLOSE is consistent."""
    from src.core.stage_trade_playbook import active_stages

    active_stages_fn = active_stages_fn or active_stages

    feat_rows = _precompute_feature_rows(risk_components, index_history)
    if not feat_rows:
        return FlexState(mode=mode)

    dates = [r["trade_date"] for r in feat_rows]
    state = FlexState(mode=mode)
    csi = map_csi300()

    for i, feat in enumerate(feat_rows):
        stages = active_stages_fn(feat)
        core_sig = bool(
            feat.get("hs300_dd60") is not None
            and 60 <= float(feat["rt"]) < 80
            and float(feat["hs300_dd60"]) <= -0.05
        )
        d = dates[i]
        rising = "RISING_HARD" in stages
        is_last = i == len(feat_rows) - 1

        # --- advance day counters (held days counted from entry_date, not signal day) ---
        if state.core.status == "open" and state.core.entry_date:
            try:
                ei = dates.index(str(state.core.entry_date)[:10])
                if i >= ei:
                    state.core.days_held = i - ei
                    state.core.days_remaining = max(0, CORE_HOLD_DAYS - state.core.days_held)
                else:
                    state.core.days_held = 0
                    state.core.days_remaining = CORE_HOLD_DAYS
            except ValueError:
                pass

        if state.satellite.status == "open" and state.satellite.entry_date:
            try:
                ei = dates.index(str(state.satellite.entry_date)[:10])
                if i >= ei:
                    state.satellite.days_held = i - ei
                    state.satellite.days_remaining = max(0, SAT_MAX_HOLD - state.satellite.days_held)
                else:
                    state.satellite.days_held = 0
                    state.satellite.days_remaining = SAT_DEFAULT_HOLD
            except ValueError:
                pass

        # --- exits: apply on historical days only; keep open on last bar for panel CLOSE ---
        if not is_last:
            if state.core.status == "open" and state.core.days_held >= CORE_HOLD_DAYS:
                state.core = SleevePos(status="flat")

            if state.satellite.status == "open":
                flip = False
                open_stage = state.satellite.stage_id or ""
                opposites = STAGE_OPPOSITES.get(open_stage, set())
                if state.satellite.days_held >= SAT_MIN_HOLD and opposites.intersection(stages):
                    flip = True
                if state.satellite.days_held >= SAT_MAX_HOLD:
                    flip = True
                if state.satellite.days_held >= SAT_DEFAULT_HOLD and not any(
                    STAGE_TIER.get(s) in {"high", "observe"} for s in stages
                ):
                    flip = True
                if flip:
                    state.satellite = SleevePos(status="flat")

        # --- core open (signal day = i; entry next session) ---
        if core_sig and state.core.status != "open":
            entry_date = dates[i + 1] if i + 1 < len(dates) else d
            state.core = SleevePos(
                status="open",
                entry_signal_date=d,
                entry_date=entry_date,
                days_held=0,
                days_remaining=CORE_HOLD_DAYS,
                stage_id="CSI300_CORE_BUY",
                names=["沪深300"],
                weights={"沪深300": 1.0},
                etf_code=csi.get("etf_code"),
            )

        # --- satellite open ---
        longs, _avoids, _sup = merge_satellite_targets(stages, rising_hard=rising)
        high_stages = [s for s in stages if STAGE_TIER.get(s) == "high"]
        observe_stages = [s for s in stages if STAGE_TIER.get(s) == "observe"]
        sat_sig = bool(longs) and (bool(high_stages) or bool(observe_stages))
        if sat_sig and state.satellite.status != "open" and longs:
            primary = next(
                (
                    s
                    for s in [
                        "CSI300_CORE_BUY",
                        "HIGH_COOLING",
                        "ENTER_70_BOUNCE",
                        "RISING_HARD",
                        "FALLING_HARD",
                    ]
                    if s in stages
                ),
                stages[0] if stages else None,
            )
            use = longs[:1] if (not high_stages and observe_stages) else longs
            weights = {x["name"]: x["weight_in_sat"] for x in use}
            ssum = sum(weights.values()) or 1.0
            weights = {k: round(v / ssum, 4) for k, v in weights.items()}
            entry_date = dates[i + 1] if i + 1 < len(dates) else d
            state.satellite = SleevePos(
                status="open",
                entry_signal_date=d,
                entry_date=entry_date,
                days_held=0,
                days_remaining=SAT_DEFAULT_HOLD,
                stage_id=primary,
                names=list(weights.keys()),
                weights=weights,
            )

        state.as_of = d

    return state


def build_risk_dashboard(
    alloc: dict[str, Any],
    core_active: bool,
    sat_names: list[str],
    sat_weights: dict[str, float],
) -> dict[str, Any]:
    w_core = float(alloc.get("w_core") or 0)
    w_sat = float(alloc.get("w_sat") or 0)
    beta = 0.0
    if core_active or w_core > 0:
        beta += w_core * SECTOR_BETA.get("沪深300", 1.0)
    for name, w in sat_weights.items():
        beta += w_sat * float(w) * SECTOR_BETA.get(name, 1.1)
    # rough daily vol heuristic: 1% * beta
    est_daily_vol = abs(beta) * 0.01
    # correlation risk flag if many high-beta growth names
    growth = {"恒生科技", "电子", "通信", "计算机", "传媒", "国防军工"}
    growth_w = sum(float(sat_weights.get(n, 0)) for n in growth)
    corr_flag = growth_w >= 0.6 and w_sat >= 0.2
    return {
        "estimated_beta": round(beta, 3),
        "estimated_daily_vol": round(est_daily_vol, 4),
        "estimated_daily_vol_cn": f"约 {est_daily_vol*100:.1f}%（固定beta启发式）",
        "total_exposure": alloc.get("total_exposure"),
        "cash": alloc.get("w_cash"),
        "growth_sat_share": round(growth_w, 3),
        "correlation_warning": corr_flag,
        "correlation_note": "卫星内成长/TMT 权重偏高，与核心同涨同跌风险上升" if corr_flag else "相关风险中性",
        "max_single_name_weight": round(
            max([w_core] + [w_sat * float(w) for w in sat_weights.values()] or [0.0]), 4
        ),
        "circuit_breaker_cn": "组合研究回撤>15% 时建议卫星清零、仅保留核心规则",
        "model_cn": "固定beta + 目标仓位的暴露估算；不是实盘风控执行器",
        "controls": [
            {"key": "max_exposure", "value": alloc.get("total_exposure"), "enforced": True},
            {"key": "max_single_name", "value": round(max([w_core] + [w_sat * float(w) for w in sat_weights.values()] or [0.0]), 4), "enforced": True},
            {"key": "research_drawdown_stop", "value": -0.15, "enforced": False},
        ],
    }


def build_flex_panel_v2(
    feat: dict[str, Any],
    stages: list[str],
    detailed: list[dict],
    core_buy_signal: bool,
    primary: dict,
    *,
    risk_components: pd.DataFrame | None = None,
    index_history: pd.DataFrame | None = None,
    mode: str = MODE_CONSERVATIVE,
    backtest_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full Flex panel with state machine, sizing, merge, minimal actions."""
    mode = mode if mode in SIZING else MODE_CONSERVATIVE
    rising = "RISING_HARD" in stages

    # Simulated state as of latest
    if risk_components is not None and not risk_components.empty:
        state = simulate_positions(risk_components, index_history, mode=mode)
        save_position_state(state)
    else:
        state = load_position_state()
        state.mode = mode

    longs, avoids, suppressed = merge_satellite_targets(stages, rising_hard=rising)

    high_stages = [s for s in stages if STAGE_TIER.get(s) == "high"]
    observe_stages = [s for s in stages if STAGE_TIER.get(s) == "observe"]
    sat_signal = bool(longs) and (bool(high_stages) or bool(observe_stages))

    # Prefer why/win_rate from detailed stage cards, preserving the stage that supplied the evidence.
    detail_by_name_stage: dict[tuple[str, str], dict] = {}
    for st in detailed:
        for sec in (st.get("sectors_long") or []) + (st.get("sectors_short") or []):
            detail_by_name_stage[(sec["name"], st.get("stage_id"))] = sec

    for item in longs:
        evidence = []
        for sid in item.get("stages") or []:
            d = detail_by_name_stage.get((item["name"], sid)) or {}
            if d:
                evidence.append(
                    {
                        "stage_id": sid,
                        "why": d.get("why"),
                        "win_rate": d.get("win_rate"),
                        "n": d.get("n"),
                        "horizon": d.get("horizon"),
                    }
                )
        if evidence:
            item["stage_evidence"] = evidence
        d = evidence[0] if len(evidence) == 1 else {}
        if d.get("why"):
            item["why"] = d["why"]
        if d.get("win_rate") is not None:
            item["win_rate"] = d["win_rate"]
        if d.get("n") is not None:
            item["n"] = d["n"]

    # Observe-only → max 1 name, flag
    observe_only = sat_signal and not high_stages and bool(observe_stages)
    if observe_only:
        longs = longs[:1]
        for item in longs:
            item["observe_only"] = True
            item["size_note"] = "小样本观察仓，权重已压低"

    # Allocation uses *intended* new exposure for display, but position-aware actions
    core_open = state.core.status == "open"
    sat_open = state.satellite.status == "open"

    # Target: signal-based desired exposure
    want_core = core_buy_signal or (core_open and state.core.days_remaining > 0)
    # If holding core past signal still want until exit
    if core_open and state.core.days_held < CORE_HOLD_DAYS:
        want_core = True

    want_sat = sat_signal or (sat_open and state.satellite.days_remaining > 0)
    if sat_open and state.satellite.days_held < SAT_MAX_HOLD and not sat_signal:
        # may still hold until event exit / max
        want_sat = True

    # For allocation weights shown as target portfolio after T+1 actions
    core_for_alloc = bool(core_buy_signal) or (
        core_open and state.core.days_held < CORE_HOLD_DAYS and not _core_should_close(state)
    )
    sat_for_alloc = bool(sat_signal) or (
        sat_open and not _sat_should_close(state, stages) and state.satellite.days_held < SAT_MAX_HOLD
    )
    # if closing today, not in alloc
    if _core_should_close(state):
        core_for_alloc = False
    if _sat_should_close(state, stages):
        sat_for_alloc = False
    if core_buy_signal and not core_open:
        core_for_alloc = True
    if sat_signal and not sat_open:
        sat_for_alloc = True

    # observe_only scales sat budget
    alloc = compute_allocation(core_for_alloc, sat_for_alloc, mode=mode)
    if observe_only and alloc["w_sat"] > 0:
        alloc["w_sat"] = round(float(alloc["w_sat"]) * 0.25, 4)
        alloc["w_cash"] = round(max(0.0, 1.0 - float(alloc["w_core"]) - float(alloc["w_sat"])), 4)
        alloc["total_exposure"] = round(float(alloc["w_core"]) + float(alloc["w_sat"]), 4)
        alloc["allocation_cn"] += " · 观察仓卫星×0.25"

    # Build absolute target weights per instrument
    sat_weights = {}
    if longs and alloc["w_sat"] > 0:
        # renorm long weights
        ssum = sum(x["weight_in_sat"] for x in longs) or 1.0
        for x in longs:
            sat_weights[x["name"]] = round(x["weight_in_sat"] / ssum, 4)

    risk_dash = build_risk_dashboard(alloc, core_for_alloc, list(sat_weights.keys()), sat_weights)

    # ---- Actions: OPEN / HOLD / CLOSE / SKIP / AVOID ----
    actions: list[dict[str, Any]] = []
    buy_list: list[dict[str, Any]] = []
    close_list: list[dict[str, Any]] = []
    hold_list: list[dict[str, Any]] = []
    avoid_list: list[dict[str, Any]] = []

    csi = map_csi300()

    # Core action
    if _core_should_close(state):
        actions.append(
            attach_etf_fields(
                {
                    "sleeve": "core",
                    "name": "沪深300",
                    "action": "CLOSE",
                    "action_cn": "到期卖出",
                    "side": "CLOSE",
                    "side_cn": "卖出",
                    "priority": "P0",
                    "entry": "下一交易日开盘",
                    "exit": "平仓",
                    "weight_target": 0.0,
                    "weight_hint": "0%",
                    "why": f"核心持有满 {CORE_HOLD_DAYS} 日，按规则卖出（CORE_MAX_HOLD 确定性路径）",
                    "close_code": "CORE_MAX_HOLD",
                    "guaranteed": True,
                    "days_held": state.core.days_held,
                }
            )
        )
        close_list.append(actions[-1])
    elif core_open and core_buy_signal:
        actions.append(
            attach_etf_fields(
                {
                    "sleeve": "core",
                    "name": "沪深300",
                    "action": "HOLD",
                    "action_cn": "持有（忽略重复信号）",
                    "side": "HOLD",
                    "side_cn": "持有",
                    "priority": "P0",
                    "weight_target": alloc["w_core"],
                    "weight_hint": f"{alloc['w_core']:.0%}",
                    "why": "主策略持仓中，忽略新开仓信号",
                    "days_held": state.core.days_held,
                    "days_remaining": state.core.days_remaining,
                    "entry": "—",
                    "exit": f"约剩 {state.core.days_remaining} 日",
                }
            )
        )
        hold_list.append(actions[-1])
    elif core_open and not core_buy_signal:
        actions.append(
            attach_etf_fields(
                {
                    "sleeve": "core",
                    "name": "沪深300",
                    "action": "HOLD",
                    "action_cn": "持有至到期",
                    "side": "HOLD",
                    "side_cn": "持有",
                    "priority": "P0",
                    "weight_target": alloc["w_core"],
                    "weight_hint": f"{alloc['w_core']:.0%}",
                    "why": "未触发新信号；按回测规则持有满期，不因阶段切换强平",
                    "days_held": state.core.days_held,
                    "days_remaining": state.core.days_remaining,
                    "entry": "—",
                    "exit": f"约剩 {state.core.days_remaining} 日",
                }
            )
        )
        hold_list.append(actions[-1])
    elif core_buy_signal and not core_open:
        actions.append(
            attach_etf_fields(
                {
                    "sleeve": "core",
                    "name": "沪深300",
                    "action": "OPEN",
                    "action_cn": "新开买入",
                    "side": "OPEN",
                    "side_cn": "买入",
                    "priority": "P0",
                    "entry": "T+1 开盘",
                    "exit": f"持有 {CORE_HOLD_DAYS} 日 → 开盘卖出",
                    "weight_target": alloc["w_core"],
                    "weight_hint": f"{alloc['w_core']:.0%}",
                    "why": "组合 Flex 核心规则（回测胜率约 65%）",
                    "win_rate": 0.6531,
                    "n": 49,
                }
            )
        )
        buy_list.append(actions[-1])
    else:
        actions.append(
            attach_etf_fields(
                {
                    "sleeve": "core",
                    "name": "沪深300",
                    "action": "FLAT",
                    "action_cn": "观望/不新开",
                    "side": "FLAT",
                    "side_cn": "观望",
                    "priority": "P0",
                    "weight_target": 0.0,
                    "weight_hint": "0%",
                    "why": "未同时满足 60≤RT<80 与 60日回撤≤-5%",
                    "entry": "—",
                    "exit": "—",
                }
            )
        )

    # Satellite CLOSE — every triggered path must land in close_list with explicit close_code
    sat_close = _sat_close_meta(state, stages)
    if sat_close:
        for name in state.satellite.names or ["卫星篮子"]:
            actions.append(
                attach_etf_fields(
                    {
                        "sleeve": "satellite",
                        "name": name,
                        "action": "CLOSE",
                        "action_cn": sat_close.get("action_cn") or "减仓/到期退出",
                        "side": "CLOSE",
                        "side_cn": "卖出",
                        "priority": sat_close.get("priority") or "P0",
                        "entry": "下一交易日开盘",
                        "exit": "平仓",
                        "weight_target": 0.0,
                        "weight_hint": "0%",
                        "why": sat_close.get("why") or "卫星退出",
                        "close_code": sat_close.get("close_code"),
                        "guaranteed": True,
                        "days_held": state.satellite.days_held,
                    }
                )
            )
            close_list.append(actions[-1])
    elif sat_open and not sat_signal:
        for name, w in (state.satellite.weights or {n: 1.0 for n in state.satellite.names}).items():
            tw = round(float(alloc["w_sat"]) * float(w), 4) if alloc["w_sat"] else 0.0
            actions.append(
                attach_etf_fields(
                    {
                        "sleeve": "satellite",
                        "name": name,
                        "action": "HOLD",
                        "action_cn": "持有卫星",
                        "side": "HOLD",
                        "side_cn": "持有",
                        "priority": "P1",
                        "weight_target": tw,
                        "weight_hint": f"{tw:.0%}" if tw else "—",
                        "why": "阶段信号减弱但仍在最短/默认持有期内",
                        "days_held": state.satellite.days_held,
                        "days_remaining": state.satellite.days_remaining,
                        "entry": "—",
                        "exit": f"最长 {SAT_MAX_HOLD} 日或事件翻转",
                    }
                )
            )
            hold_list.append(actions[-1])
    elif sat_open and sat_signal:
        # HOLD existing; if names differ, still hold old until exit schedule (no mid-basket churn)
        for name, w in (state.satellite.weights or {}).items():
            tw = round(float(alloc["w_sat"]) * float(w), 4)
            actions.append(
                attach_etf_fields(
                    {
                        "sleeve": "satellite",
                        "name": name,
                        "action": "HOLD",
                        "action_cn": "持有（忽略重复开仓）",
                        "side": "HOLD",
                        "side_cn": "持有",
                        "priority": "P1",
                        "weight_target": tw,
                        "weight_hint": f"{tw:.0%}",
                        "why": "卫星持仓中，不重叠新开",
                        "days_held": state.satellite.days_held,
                        "days_remaining": state.satellite.days_remaining,
                        "entry": "—",
                        "exit": f"约剩 {state.satellite.days_remaining} 日 / 事件退出",
                    }
                )
            )
            hold_list.append(actions[-1])
    elif sat_signal and not sat_open:
        for x in longs:
            tw = round(float(alloc["w_sat"]) * float(sat_weights.get(x["name"], 0)), 4)
            if tw <= 0 and alloc["w_sat"] <= 0:
                continue
            actions.append(
                attach_etf_fields(
                    {
                        "sleeve": "satellite",
                        "name": x["name"],
                        "action": "OPEN",
                        "action_cn": "新开超配",
                        "side": "OVERWEIGHT",
                        "side_cn": "超配买入",
                        "priority": "P1" if (x.get("n") or 0) >= MIN_N_FULL else "P2",
                        "entry": "T+1 开盘",
                        "exit": f"{SAT_MIN_HOLD}–{SAT_MAX_HOLD} 日（事件可提前）",
                        "weight_target": tw,
                        "weight_hint": f"{tw:.0%}",
                        "weight_in_sat": x.get("weight_in_sat"),
                        "why": x.get("why"),
                        "win_rate": x.get("win_rate"),
                        "n": x.get("n"),
                        "score": x.get("score"),
                        "observe_only": x.get("observe_only", False),
                        "size_note": x.get("size_note"),
                        "semantic": "相对超额偏好→绝对仓位研究指令；熊市仍可能绝对亏损",
                    }
                )
            )
            buy_list.append(actions[-1])

    # AVOID (conditional) — never show as raw SELL without condition
    for a in avoids:
        q = _quality_of(a["name"])
        # still attach even if weak
        actions.append(
            attach_etf_fields(
                {
                    **a,
                    "action": "AVOID",
                    "action_cn": "回避/条件减配",
                    "side": "AVOID",
                    "priority": "P2",
                    "weight_target": 0.0,
                    "weight_hint": "0%（若持有）",
                    "entry": "—",
                    "exit": "—",
                }
            )
        )
        avoid_list.append(actions[-1])

    # Minimal action set: 1 core + top 2 sat OPEN/CLOSE by |weight|
    minimal = _select_minimal_actions(actions)

    # Core sleeve display
    if any(a.get("action") == "CLOSE" and a.get("sleeve") == "core" for a in actions):
        core_action, core_action_cn, core_tone = "CLOSE", "到期卖出", "sell"
        core_detail = "主策略持有期满，下一交易日开盘卖出。"
        core_active_flag = False
    elif any(a.get("action") == "OPEN" and a.get("sleeve") == "core" for a in actions):
        core_action, core_action_cn, core_tone = "OPEN", "新开买入", "buy"
        core_detail = f"T 日收盘满足条件 → T+1 开盘买入；持有 {CORE_HOLD_DAYS} 日。"
        core_active_flag = True
    elif any(a.get("action") == "HOLD" and a.get("sleeve") == "core" for a in actions):
        core_action, core_action_cn, core_tone = "HOLD", "持有中", "buy"
        core_detail = f"已持有约 {state.core.days_held} 日，剩余约 {state.core.days_remaining} 日。"
        core_active_flag = True
    else:
        core_action, core_action_cn, core_tone = "FLAT", "观望/不新开", "wait"
        core_detail = "未同时满足 60≤RT<80 与 60日回撤≤-5%。"
        core_active_flag = False

    sat_stage_ids = [s for s in stages if s in FLEX_SAT_LONG]
    sat_stage_cn = " + ".join(
        next((d.get("name_cn") for d in detailed if d.get("stage_id") == s), s) for s in sat_stage_ids
    ) or "无"

    if any(a.get("action") == "CLOSE" and a.get("sleeve") == "satellite" for a in actions):
        sat_status = "退出"
        sat_active_flag = False
        sat_tone = "sell"
    elif any(a.get("action") in {"OPEN", "OVERWEIGHT"} and a.get("sleeve") == "satellite" for a in actions):
        sat_status = "新开" + ("（观察仓）" if observe_only else "")
        sat_active_flag = True
        sat_tone = "buy"
    elif any(a.get("action") == "HOLD" and a.get("sleeve") == "satellite" for a in actions):
        sat_status = "持有中"
        sat_active_flag = True
        sat_tone = "buy"
    else:
        sat_status = "未激活"
        sat_active_flag = False
        sat_tone = "wait"

    bt = backtest_stats or DEFAULT_BACKTEST_STATS
    # Prefer mode-specific stats for headline
    mode_bt = (bt.get(mode) or bt.get("aggressive") or {})
    full = mode_bt.get("full_sample") or (bt.get("aggressive") or {}).get("full_sample") or {}

    trade_dates: list[str] = []
    if risk_components is not None and not risk_components.empty and "trade_date" in risk_components.columns:
        trade_dates = (
            risk_components.sort_values("trade_date")["trade_date"]
            .astype(str)
            .str.slice(0, 10)
            .drop_duplicates()
            .tolist()
        )
    exit_plan = build_sleeve_exit_plan(state, stages, trade_dates=trade_dates)
    # Persist exit_due_date into position state file
    state.as_of = str(feat.get("trade_date") or state.as_of or "")[:10] or state.as_of
    save_position_state(state)

    if alloc["allocation_mode"] == "BOTH":
        headline = "组合 Flex v2：核心 + 卫星（多阶段合并）"
        status_cn = "双仓"
    elif alloc["allocation_mode"] == "CORE_ONLY":
        headline = "组合 Flex v2：核心仓"
        status_cn = "核心"
    elif alloc["allocation_mode"] == "SAT_ONLY":
        headline = "组合 Flex v2：卫星仓" + ("（观察）" if observe_only else "")
        status_cn = "卫星"
    else:
        # still may have CLOSE actions
        if close_list:
            headline = "组合 Flex v2：执行平仓"
            status_cn = "平仓"
        else:
            headline = "组合 Flex v2：暂无开仓信号"
            status_cn = "观望"

    if hold_list and not buy_list and not close_list:
        status_cn = "持有"
        headline = "组合 Flex v2：持仓管理（无新开）"

    modes_payload = {
        MODE_CONSERVATIVE: {
            **SIZING[MODE_CONSERVATIVE],
            "active": mode == MODE_CONSERVATIVE,
            "stats": bt.get("conservative") or {},
        },
        MODE_AGGRESSIVE: {
            **SIZING[MODE_AGGRESSIVE],
            "active": mode == MODE_AGGRESSIVE,
            "stats": bt.get("aggressive") or {},
        },
    }

    return {
        "version": "flex_v2",
        "status": status_cn,
        "status_code": alloc["allocation_mode"],
        "headline": headline,
        "as_of": feat.get("trade_date"),
        "execution_cn": "信号日 T 收盘确认 → 下一交易日开盘执行",
        "hold_days": CORE_HOLD_DAYS,
        "hold_days_sat_cn": f"卫星 {SAT_MIN_HOLD}–{SAT_MAX_HOLD} 日（最短{SAT_MIN_HOLD}，事件可提前，最长{SAT_MAX_HOLD}）",
        "allocation_cn": alloc["allocation_cn"],
        "allocation_mode": alloc["allocation_mode"],
        "allocation": alloc,
        "mode": mode,
        "modes": modes_payload,
        "market_state": {
            "rt": feat.get("rt"),
            "rt_d1": feat.get("rt_d1"),
            "rt_d5": feat.get("rt_d5"),
            "hs300_dd60": feat.get("hs300_dd60"),
            "regime_cn": feat.get("regime_cn"),
        },
        "active_stages": stages,
        "suppressed_stages": suppressed,
        "merge_note_cn": (
            f"多阶段合并：{', '.join(sat_stage_ids) or '无'}；"
            f"被门控压制：{', '.join(suppressed) or '无'}"
            + ("；升温日不追恒科/电子/计算机（除非 CORE 同步）" if rising else "")
        ),
        "position_state": state.to_dict(),
        "exit_plan": exit_plan,
        "core": {
            "sleeve": "core",
            "name": "沪深300 主策略",
            "action": core_action,
            "action_cn": core_action_cn,
            "tone": core_tone,
            "detail": core_detail,
            "rule": f"60≤RT<80 且 60日回撤≤-5%；持有{CORE_HOLD_DAYS}日",
            "active": core_active_flag,
            "etf_code": csi["etf_code"],
            "etf_name": csi["etf_name"],
            "etf_label": csi["etf_label"],
            "weight_target": alloc["w_core"],
            "position": state.core.to_dict(),
        },
        "satellite": {
            "sleeve": "satellite",
            "name": "板块超配卫星",
            "active": sat_active_flag,
            "status_cn": sat_status,
            "tone": sat_tone,
            "stage_id": state.satellite.stage_id or (high_stages[0] if high_stages else (observe_stages[0] if observe_stages else None)),
            "stage_cn": sat_stage_cn,
            "stage_ids": sat_stage_ids,
            "buy": [attach_etf_fields(x) for x in longs],
            "avoid": avoid_list,
            "detail": (
                "高置信阶段全量；小样本为观察仓；"
                "按得分×映射质量加权；弱代理不进默认篮子；long-only 不做空。"
            ),
            "weights": sat_weights,
            "weight_target": alloc["w_sat"],
            "observe_only": observe_only,
            "position": state.satellite.to_dict(),
        },
        # Backward-compatible lists
        "buy_list": buy_list,
        "sell_list": close_list,  # only real closes
        "hold_list": hold_list,
        "avoid_list": avoid_list,
        "close_list": close_list,
        "minimal_actions": minimal,
        "all_actions": actions,
        "risk_dashboard": risk_dash,
        "sector_etf_map_version": "config/sector_etf_map.yml",
        "quality_policy": {
            "good": 1.0,
            "proxy": 0.70,
            "weak": 0.0,
            "note_cn": "弱代理不进默认买入；主题代理权重×0.7；回测中 proxy 正收益折扣、负收益放大",
        },
        "backtest": bt,
        "backtest_display": {
            "mode": mode,
            "win_rate": full.get("win_rate"),
            "ann_return": full.get("ann_return"),
            "core_only_ann": (bt.get("core_only") or {}).get("ann_return"),
            "cost_stress": bt.get("cost_stress") or {},
            "compare_cn": "建议并排对照「仅核心」；进取 Flex 收益偏高、实盘折扣大",
        },
        "semantics": {
            "OPEN": "新开绝对仓位",
            "HOLD": "已有仓位继续持有",
            "CLOSE": "到期或事件退出卖出",
            "OVERWEIGHT": "卫星超配（研究绝对仓位，语义来自相对超额）",
            "AVOID": "无持仓则不操作；有持仓则减至 0",
            "FLAT": "空仓观望",
        },
        "disclaimer": (
            "研究回测指令，非投资建议。"
            "默认展示进取仓位；弱代理已剔除；"
            "超额≠绝对收益；ETF 映射见 config/sector_etf_map.yml。"
        ),
        "primary_stage_cn": primary.get("name_cn"),
    }


def _core_should_close(state: FlexState) -> bool:
    return state.core.status == "open" and state.core.days_held >= CORE_HOLD_DAYS


def _sat_close_meta(state: FlexState, stages: list[str]) -> dict[str, Any] | None:
    """Return close reason metadata when satellite must exit; None if still hold.

    Codes (deterministic evaluation order):
      MAX_HOLD      — days_held >= SAT_MAX_HOLD (always fires if held long enough)
      EVENT_FLIP    — days_held >= SAT_MIN_HOLD and opposite stage present
      DEFAULT_NO_STAGE — days_held >= SAT_DEFAULT_HOLD and no high/observe stage
    """
    if state.satellite.status != "open":
        return None
    held = int(state.satellite.days_held or 0)
    open_stage = state.satellite.stage_id or ""
    opposites = STAGE_OPPOSITES.get(open_stage, set())
    hit_opp = sorted(opposites.intersection(stages))
    has_live_stage = any(STAGE_TIER.get(s) in {"high", "observe"} for s in stages)

    if held >= SAT_MAX_HOLD:
        return {
            "close_code": "MAX_HOLD",
            "priority": "P0",
            "action_cn": "最长持有到期卖出",
            "why": f"卫星已持有 {held} 日 ≥ 最长 {SAT_MAX_HOLD} 日，必须平仓（确定性路径）",
            "guaranteed": True,
        }
    if held >= SAT_MIN_HOLD and hit_opp:
        return {
            "close_code": "EVENT_FLIP",
            "priority": "P0",
            "action_cn": "事件翻转卖出",
            "why": (
                f"卫星已持有 {held} 日 ≥ 最短 {SAT_MIN_HOLD} 日，"
                f"开仓阶段 {open_stage or '—'} 命中对立 {','.join(hit_opp)}，提前平仓"
            ),
            "guaranteed": True,
            "flip_stages": hit_opp,
        }
    if held >= SAT_DEFAULT_HOLD and not has_live_stage:
        return {
            "close_code": "DEFAULT_NO_STAGE",
            "priority": "P1",
            "action_cn": "默认持有期满卖出",
            "why": (
                f"卫星已持有 {held} 日 ≥ 默认 {SAT_DEFAULT_HOLD} 日，"
                f"且当日无 high/observe 阶段，按默认路径平仓"
            ),
            "guaranteed": True,
        }
    return None


def _sat_should_close(state: FlexState, stages: list[str]) -> bool:
    return _sat_close_meta(state, stages) is not None


def _nth_trade_date(trade_dates: list[str], entry_date: str | None, held_offset: int) -> str | None:
    """Date when days_held would equal held_offset if entry_date is hold day 0."""
    if not entry_date or not trade_dates:
        return None
    ed = str(entry_date)[:10]
    try:
        ei = trade_dates.index(ed)
    except ValueError:
        return None
    j = ei + int(held_offset)
    if 0 <= j < len(trade_dates):
        return trade_dates[j]
    # Extrapolate weekdays beyond known history
    from datetime import datetime, timedelta

    d = datetime.strptime(trade_dates[-1], "%Y-%m-%d").date()
    need = j - (len(trade_dates) - 1)
    while need > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            need -= 1
    return d.isoformat()


def build_sleeve_exit_plan(
    state: FlexState,
    stages: list[str],
    *,
    trade_dates: list[str] | None = None,
) -> dict[str, Any]:
    """Publish deterministic exit calendar so the desk can always show countdown / tips."""
    dates = list(trade_dates or [])
    sat = state.satellite
    core = state.core
    sat_meta = _sat_close_meta(state, stages) if sat.status == "open" else None

    sat_plan: dict[str, Any] = {
        "status": sat.status,
        "entry_signal_date": sat.entry_signal_date,
        "entry_date": sat.entry_date,
        "days_held": sat.days_held,
        "stage_id": sat.stage_id,
        "rules": {
            "min_hold": SAT_MIN_HOLD,
            "default_hold": SAT_DEFAULT_HOLD,
            "max_hold": SAT_MAX_HOLD,
            "opposites": sorted(STAGE_OPPOSITES.get(sat.stage_id or "", set())),
        },
        "triggered_close": sat_meta,
        "paths": {},
        "note_cn": (
            "四只卫星同一 sleeve 同日进出。"
            "MAX_HOLD 为确定性到期；EVENT_FLIP / DEFAULT_NO_STAGE 条件满足当天必进 close_list。"
        ),
    }
    if sat.status == "open" and sat.entry_date:
        sat_plan["paths"] = {
            "event_earliest_signal_date": _nth_trade_date(dates, sat.entry_date, SAT_MIN_HOLD),
            "default_signal_date": _nth_trade_date(dates, sat.entry_date, SAT_DEFAULT_HOLD),
            "max_signal_date": _nth_trade_date(dates, sat.entry_date, SAT_MAX_HOLD),
            "max_exec_next_open": _nth_trade_date(dates, sat.entry_date, SAT_MAX_HOLD + 1),
            "default_exec_next_open": _nth_trade_date(dates, sat.entry_date, SAT_DEFAULT_HOLD + 1),
            "event_exec_next_open": _nth_trade_date(dates, sat.entry_date, SAT_MIN_HOLD + 1),
        }
        # Authoritative due date for longest path (always scheduled at open).
        sat.exit_due_date = sat_plan["paths"].get("max_signal_date")
        sat_plan["exit_due_date"] = sat.exit_due_date
        sat_plan["days_to_max"] = max(0, SAT_MAX_HOLD - int(sat.days_held or 0))

    core_plan: dict[str, Any] = {
        "status": core.status,
        "entry_signal_date": core.entry_signal_date,
        "entry_date": core.entry_date,
        "days_held": core.days_held,
        "hold_days": CORE_HOLD_DAYS,
        "triggered_close": (
            {
                "close_code": "CORE_MAX_HOLD",
                "why": f"核心已持有 {core.days_held} 日 ≥ {CORE_HOLD_DAYS} 日，必须平仓",
                "guaranteed": True,
            }
            if _core_should_close(state)
            else None
        ),
    }
    if core.status == "open" and core.entry_date:
        core_plan["max_signal_date"] = _nth_trade_date(dates, core.entry_date, CORE_HOLD_DAYS)
        core_plan["max_exec_next_open"] = _nth_trade_date(dates, core.entry_date, CORE_HOLD_DAYS + 1)
        core.exit_due_date = core_plan.get("max_signal_date")
        core_plan["exit_due_date"] = core.exit_due_date
        core_plan["days_to_max"] = max(0, CORE_HOLD_DAYS - int(core.days_held or 0))

    return {
        "as_of": state.as_of,
        "core": core_plan,
        "satellite": sat_plan,
        "guarantee_cn": (
            "日更成功且 as_of=当日时：任一 close_code 触发必写入 close_list；"
            "执行台必须展示策略平仓提示（本机未点买也可看，点买后可记账平仓）。"
            "日更失败则页面不会刷新——需看 Actions。"
        ),
    }


def _select_minimal_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Max 1 core + 2 satellite actionable (OPEN/CLOSE); include core HOLD if only hold."""
    core_acts = [a for a in actions if a.get("sleeve") == "core" and a.get("action") in {"OPEN", "CLOSE", "HOLD"}]
    sat_acts = [
        a
        for a in actions
        if a.get("sleeve") == "satellite" and a.get("action") in {"OPEN", "CLOSE", "OVERWEIGHT"}
    ]
    # rank sat by weight_target
    sat_acts = sorted(sat_acts, key=lambda x: abs(float(x.get("weight_target") or 0)), reverse=True)[:2]
    out: list[dict[str, Any]] = []
    if core_acts:
        # prefer OPEN/CLOSE over HOLD for minimal "must do"
        prefer = [a for a in core_acts if a.get("action") in {"OPEN", "CLOSE"}]
        out.append(prefer[0] if prefer else core_acts[0])
    out.extend(sat_acts)
    return out


def load_backtest_stats_file() -> dict[str, Any]:
    path = CALCULATED / "flex_backtest_stats.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged = dict(DEFAULT_BACKTEST_STATS)
                merged.update(data)
                return merged
        except Exception:
            pass
    return dict(DEFAULT_BACKTEST_STATS)
