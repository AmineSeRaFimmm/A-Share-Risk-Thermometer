#!/usr/bin/env python3
"""Dense-grid Flex portfolio backtest through 2026-07-14.

- Official risk_temperature used when available.
- Missing days after last official (through 2026-07-14) filled with nowcast estimates
  from latest.json / nowcast_history / linear bridge.
- Core signal grid is fully enumerated (dense).
- Satellite hold-window grid is dense.
- Portfolio sizing/cost/mode grid is dense on top of daily sleeves.

Outputs under research/output/flex_dense_grid/.
"""
from __future__ import annotations

import itertools
import json
import math
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.backtest_core_plus_sectors import (  # noqa: E402
    OOS_SPLIT,
    TRADING_DAYS,
    annualized,
    detect_stages_row,
    load_aligned,
    max_dd,
)
from src.core.flex_engine import (  # noqa: E402
    FLEX_SAT_LONG,
    QUALITY_RETURN_HAIRCUT,
    QUALITY_WEIGHT,
    STAGE_OPPOSITES,
    STAGE_TIER,
    merge_satellite_targets,
)
from src.core.sector_etf_map import map_sector  # noqa: E402

OUT = ROOT / "research/output/flex_dense_grid"
END_DATE = pd.Timestamp("2026-07-14")
OOS_START = pd.Timestamp("2024-01-01")


def quality_of(name: str) -> str:
    return str(map_sector(name).get("quality") or "missing")


def stats_from_daily(daily: np.ndarray, trade_rets: list[float] | None = None) -> dict:
    daily = np.asarray(daily, dtype=float)
    equity = np.cumprod(1.0 + daily)
    total = float(equity[-1] - 1.0) if len(equity) else 0.0
    n = len(daily)
    rets = trade_rets if trade_rets is not None else []
    return {
        "total_return": total,
        "ann_return": float(annualized(total, n)) if n else float("nan"),
        "max_dd": float(max_dd(equity)) if len(equity) else float("nan"),
        "sharpe": float(np.mean(daily) / np.std(daily, ddof=1) * math.sqrt(TRADING_DAYS))
        if n > 2 and np.std(daily, ddof=1) > 0
        else float("nan"),
        "trade_count": int(len(rets)),
        "win_rate": float(np.mean([r > 0 for r in rets])) if rets else float("nan"),
        "avg_trade": float(np.mean(rets)) if rets else float("nan"),
        "exposure_ratio": float(np.mean(daily != 0.0)) if n else 0.0,
    }


def oos_daily(daily: np.ndarray, dates: pd.Series, start: pd.Timestamp = OOS_START) -> np.ndarray:
    out = daily.copy()
    mask = dates.values < np.datetime64(start)
    out[mask] = 0.0
    return out


def load_nowcast_rt_points() -> dict[pd.Timestamp, float]:
    """Map trade_date -> estimated RT from available nowcast sources."""
    pts: dict[pd.Timestamp, float] = {}
    # latest.json (often NOWCAST for the current session day)
    for path in (
        ROOT / "docs/data/latest.json",
        ROOT / "data/site/latest.json",
    ):
        if not path.exists():
            continue
        try:
            latest = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        td = latest.get("trade_date") or latest.get("nowcast", {}).get("trade_date")
        rt = latest.get("risk_temperature")
        if latest.get("nowcast") and latest["nowcast"].get("risk_temperature") is not None:
            rt = latest["nowcast"]["risk_temperature"]
        if td is not None and rt is not None and np.isfinite(float(rt)):
            pts[pd.Timestamp(td).normalize()] = float(rt)
    # nowcast history rows
    for path in (
        ROOT / "docs/data/nowcast_history.json",
        ROOT / "data/site/nowcast_history.json",
    ):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in payload.get("rows") or []:
            td = row.get("date") or row.get("trade_date")
            rt = row.get("risk_temperature_estimated") or row.get("risk_temperature")
            if td is None or rt is None:
                continue
            try:
                pts[pd.Timestamp(td).normalize()] = float(rt)
            except Exception:
                continue
    # risk_temperature_nowcast.csv if non-empty
    nc_path = ROOT / "data/calculated/risk_temperature_nowcast.csv"
    if nc_path.exists():
        try:
            nc = pd.read_csv(nc_path)
            if not nc.empty and "trade_date" in nc.columns:
                for _, row in nc.iterrows():
                    rt = row.get("risk_temperature_estimated", row.get("risk_temperature"))
                    if pd.isna(rt):
                        continue
                    pts[pd.Timestamp(row["trade_date"]).normalize()] = float(rt)
        except Exception:
            pass
    return pts


def extend_aligned_to_end(df: pd.DataFrame, meta: dict, end: pd.Timestamp = END_DATE) -> tuple[pd.DataFrame, dict, dict]:
    """Extend CSI + RT calendar to `end`, filling missing RT with nowcast estimates."""
    df = df.copy().sort_values("trade_date").reset_index(drop=True)
    last_off = pd.Timestamp(df["trade_date"].max()).normalize()
    audit = {
        "official_end": str(last_off.date()),
        "target_end": str(end.date()),
        "nowcast_points": {str(k.date()): v for k, v in sorted(load_nowcast_rt_points().items())},
        "filled_days": [],
    }
    if last_off >= end:
        # still tag any day that used nowcast if we force replace — no fill needed
        audit["note"] = "official already covers target end"
        return df, meta, audit

    # CSI prices from raw index through end
    idx = pd.read_csv(ROOT / "data/raw/indices/sh000300.csv")
    idx["date"] = pd.to_datetime(idx["date"])
    for c in ["open", "close", "high", "low"]:
        if c in idx.columns:
            idx[c] = pd.to_numeric(idx[c], errors="coerce")
    idx = idx.sort_values("date").drop_duplicates("date")
    extra = idx[(idx["date"] > last_off) & (idx["date"] <= end)].copy()
    if extra.empty:
        # If no CSI bar yet for target day, synthesize last available + nowcast-only day when needed
        audit["note"] = "no HS300 bars after official RT end; cannot price beyond last CSI bar"
        # Still allow RT-only extension is useless without prices; clamp end to last CSI
        csi_max = pd.Timestamp(idx["date"].max()).normalize()
        audit["effective_end"] = str(csi_max.date())
        end = min(end, csi_max)
        extra = idx[(idx["date"] > last_off) & (idx["date"] <= end)].copy()

    nowcast = load_nowcast_rt_points()
    last_rt = float(df.iloc[-1]["rt"])
    # bridge: for days without explicit nowcast, linearly interpolate toward latest known nowcast
    bridge_target = None
    bridge_date = None
    if nowcast:
        # prefer latest nowcast on/after last_off
        future = {d: v for d, v in nowcast.items() if d > last_off}
        if future:
            bridge_date = max(future)
            bridge_target = future[bridge_date]
        else:
            bridge_date = max(nowcast)
            bridge_target = nowcast[bridge_date]

    new_rows = []
    extra_dates = [pd.Timestamp(d).normalize() for d in extra["date"].tolist()]
    n_gap = max(len(extra_dates), 1)
    for k, (_, prow) in enumerate(extra.iterrows()):
        td = pd.Timestamp(prow["date"]).normalize()
        if td in nowcast:
            rt = float(nowcast[td])
            src = "NOWCAST_POINT"
        elif bridge_target is not None and bridge_date is not None and bridge_date > last_off:
            # linear bridge last_rt → bridge_target across gap days up to bridge_date
            # position among extra dates
            frac = (k + 1) / n_gap
            rt = float(last_rt + (bridge_target - last_rt) * frac)
            src = "NOWCAST_BRIDGE"
        else:
            rt = last_rt
            src = "FFILL_OFFICIAL"
        new_rows.append(
            {
                "trade_date": td,
                "rt": rt,
                "csi_open": float(prow["open"]),
                "csi_close": float(prow["close"]),
                "csi_high": float(prow.get("high", prow["close"])),
                "csi_low": float(prow.get("low", prow["close"])),
                "rt_source": src,
            }
        )
        audit["filled_days"].append({"trade_date": str(td.date()), "rt": rt, "source": src})

    if not new_rows:
        audit["note"] = "no rows appended"
        return df, meta, audit

    add = pd.DataFrame(new_rows)
    # rebuild features on concatenated series
    keep_cols = [c for c in df.columns if c not in ("rt_d1", "rt_d5", "rt_rollmax_10", "prev_rt", "dd60", "next_open", "next_date")]
    base = df[keep_cols].copy()
    if "rt" not in base.columns:
        base["rt"] = base["risk_temperature"]
    for c in ["csi_open", "csi_close", "csi_high", "csi_low", "rt"]:
        if c not in base.columns and c in df.columns:
            base[c] = df[c]
    base["rt_source"] = "OFFICIAL"
    add2 = add.copy()
    # align columns
    for c in base.columns:
        if c not in add2.columns:
            add2[c] = np.nan
    add2 = add2[base.columns]
    out = pd.concat([base, add2], ignore_index=True).sort_values("trade_date").reset_index(drop=True)
    out["rt"] = pd.to_numeric(out["rt"], errors="coerce")
    if "risk_temperature" in out.columns:
        out["risk_temperature"] = out["rt"]
    out["csi_open"] = pd.to_numeric(out["csi_open"], errors="coerce")
    out["csi_close"] = pd.to_numeric(out["csi_close"], errors="coerce")
    out["next_open"] = out["csi_open"].shift(-1)
    out["next_date"] = out["trade_date"].shift(-1)
    out["dd60"] = out["csi_close"] / out["csi_close"].rolling(60, min_periods=20).max() - 1.0
    out["prev_rt"] = out["rt"].shift(1)
    out["rt_d1"] = out["rt"].diff()
    out["rt_d5"] = out["rt"] - out["rt"].shift(5)
    out["rt_rollmax_10"] = out["rt"].rolling(10, min_periods=5).max()

    # reindex sector panels to new calendar (ffill 1 day)
    dates = out["trade_date"].tolist()
    sec = pd.read_csv(ROOT / "data/normalized/sw_level1_sector_history.csv")
    sec["date"] = pd.to_datetime(sec["date"])
    sec["close"] = pd.to_numeric(sec["close"], errors="coerce")
    sec["open"] = pd.to_numeric(sec.get("open"), errors="coerce")
    if sec["open"].isna().all():
        sec["open"] = sec["close"]
    hstech_path = ROOT / "data/raw/indices/hstech.csv"
    if hstech_path.exists():
        hs = pd.read_csv(hstech_path)
        hs["date"] = pd.to_datetime(hs["date"])
        hs["close"] = pd.to_numeric(hs["close"], errors="coerce")
        hs["open"] = pd.to_numeric(hs.get("open"), errors="coerce")
        if "open" not in hs.columns or hs["open"].isna().all():
            hs["open"] = hs["close"]
        hs["name"] = "恒生科技"
        sec = pd.concat(
            [sec[["date", "name", "open", "close"]], hs[["date", "name", "open", "close"]]],
            ignore_index=True,
        )
    sector_open: dict[str, np.ndarray] = {}
    sector_close: dict[str, np.ndarray] = {}
    for name, g in sec.groupby("name"):
        g = g.sort_values("date").drop_duplicates("date").set_index("date").reindex(dates)
        sector_open[str(name)] = g["open"].ffill(limit=2).to_numpy(dtype=float)
        sector_close[str(name)] = g["close"].ffill(limit=2).to_numpy(dtype=float)
    meta2 = {"sector_open": sector_open, "sector_close": sector_close, "names": sorted(sector_open.keys())}
    audit["extended_end"] = str(pd.Timestamp(out["trade_date"].max()).date())
    audit["n_rows"] = int(len(out))
    return out, meta2, audit


def simulate_core_daily(
    rt: np.ndarray,
    dd: np.ndarray,
    csi_open: np.ndarray,
    csi_close: np.ndarray,
    *,
    rt_low: float,
    rt_high: float,
    dd_max: float,
    hold_days: int,
    buy_cost: float,
    sell_cost: float,
) -> tuple[np.ndarray, list[float]]:
    n = len(rt)
    daily = np.zeros(n, dtype=float)
    rets: list[float] = []
    next_free = 0
    for i in range(n - 2):
        if i < next_free:
            continue
        if not (np.isfinite(rt[i]) and np.isfinite(dd[i])):
            continue
        if not (rt_low <= rt[i] < rt_high and dd[i] <= dd_max):
            continue
        if not np.isfinite(csi_open[i + 1]):
            continue
        entry_i = i + 1
        exit_i = min(entry_i + hold_days, n - 1)
        px_in = csi_open[entry_i]
        px_out = csi_open[exit_i] if exit_i < n and np.isfinite(csi_open[exit_i]) else csi_close[min(exit_i, n - 1)]
        if not (np.isfinite(px_in) and np.isfinite(px_out)):
            continue
        r = (px_out * (1 - sell_cost)) / (px_in * (1 + buy_cost)) - 1.0
        hold = max(1, exit_i - entry_i)
        daily_r = (1.0 + r) ** (1.0 / hold) - 1.0
        daily[entry_i:exit_i] = daily_r
        rets.append(float(r))
        next_free = exit_i + 1
    return daily, rets


def precompute_stages(df: pd.DataFrame) -> list[list[str]]:
    return [detect_stages_row(df.iloc[i]) for i in range(len(df))]


def simulate_sat_daily(
    df: pd.DataFrame,
    meta: dict,
    stages_all: list[list[str]],
    *,
    sat_min: int,
    sat_default: int,
    sat_max: int,
    buy_cost: float,
    sell_cost: float,
    apply_haircut: bool,
    event_exit: bool,
) -> tuple[np.ndarray, list[float]]:
    n = len(df)
    sector_open = meta["sector_open"]
    sector_close = meta["sector_close"]
    daily = np.zeros(n, dtype=float)
    rets: list[float] = []

    def ret_asset(opens, closes, entry_i, exit_i, name: str) -> float | None:
        if entry_i >= len(opens) or entry_i < 0:
            return None
        px_in = opens[entry_i]
        if not np.isfinite(px_in):
            return None
        if exit_i < len(opens) and np.isfinite(opens[exit_i]):
            px_out = opens[exit_i]
        else:
            px_out = closes[min(exit_i, len(closes) - 1)]
        if not np.isfinite(px_out):
            return None
        r = (px_out * (1 - sell_cost)) / (px_in * (1 + buy_cost)) - 1.0
        if apply_haircut:
            q = quality_of(name)
            if QUALITY_WEIGHT.get(q, 0) <= 0:
                return None
            r *= QUALITY_RETURN_HAIRCUT.get(q, 0.85)
        return float(r)

    i = 0
    while i < n - 2:
        stages = stages_all[i]
        rising = "RISING_HARD" in stages
        longs, _av, _sup = merge_satellite_targets(list(stages), rising_hard=rising)
        high = [s for s in stages if STAGE_TIER.get(s) == "high"]
        obs = [s for s in stages if STAGE_TIER.get(s) == "observe"]
        if not longs or (not high and not obs):
            i += 1
            continue
        if not high and obs:
            longs = longs[:1]
        use = [x for x in longs if x["name"] in sector_open and QUALITY_WEIGHT.get(quality_of(x["name"]), 0) > 0]
        if not use:
            i += 1
            continue
        primary = next(
            (
                s
                for s in ["CSI300_CORE_BUY", "HIGH_COOLING", "ENTER_70_BOUNCE", "RISING_HARD", "FALLING_HARD"]
                if s in stages
            ),
            stages[0] if stages else "",
        )
        entry_i = i + 1
        exit_i = min(entry_i + sat_default, n - 1)
        if event_exit:
            for k in range(entry_i + sat_min, min(entry_i + sat_max, n - 1) + 1):
                st_sig = stages_all[k - 1] if k - 1 >= 0 else stages_all[min(k, n - 1)]
                held = k - entry_i
                opposites = STAGE_OPPOSITES.get(primary, set())
                if held >= sat_min and opposites.intersection(st_sig):
                    exit_i = k
                    break
                if held >= sat_max:
                    exit_i = k
                    break
                if held >= sat_default and not any(STAGE_TIER.get(s) in {"high", "observe"} for s in st_sig):
                    exit_i = k
                    break
            else:
                exit_i = min(entry_i + sat_max, n - 1)

        trade_rets = []
        weights = []
        for x in use:
            r = ret_asset(sector_open[x["name"]], sector_close[x["name"]], entry_i, exit_i, x["name"])
            if r is not None:
                trade_rets.append(r)
                weights.append(max(float(x.get("weight_in_sat") or 0.0), 1e-6))
        if not trade_rets:
            i += 1
            continue
        w = np.asarray(weights, dtype=float)
        w = w / w.sum()
        sat_ret = float(np.dot(w, np.asarray(trade_rets, dtype=float)))
        hold = max(1, exit_i - entry_i)
        daily_r = (1.0 + sat_ret) ** (1.0 / hold) - 1.0
        daily[entry_i:exit_i] = daily_r
        rets.append(sat_ret)
        i = exit_i + 1
    return daily, rets


def combine_port(
    core_daily: np.ndarray,
    sat_daily: np.ndarray,
    *,
    w_core: float,
    w_sat: float,
    total_cap: float,
    flex_single_full: bool,
) -> np.ndarray:
    c = np.asarray(core_daily, dtype=float)
    s = np.asarray(sat_daily, dtype=float)
    c_on = c != 0.0
    s_on = s != 0.0
    wc = np.where(c_on, w_core, 0.0)
    ws = np.where(s_on, w_sat, 0.0)
    if flex_single_full:
        only_c = c_on & ~s_on
        only_s = s_on & ~c_on
        wc = np.where(only_c, 1.0, wc)
        ws = np.where(only_c, 0.0, ws)
        wc = np.where(only_s, 0.0, wc)
        ws = np.where(only_s, 1.0, ws)
    total = wc + ws
    if total_cap > 0:
        scale = np.ones_like(total)
        over = total > total_cap
        scale[over] = total_cap / total[over]
        wc = wc * scale
        ws = ws * scale
    return wc * c + ws * s


def score_row(st: dict) -> float:
    """Rank score: prefer OOS return, penalize drawdown, require trades."""
    oos_ann = st.get("oos_ann_return")
    ann = st.get("ann_return")
    dd = st.get("max_dd")
    n = st.get("trade_count") or 0
    if n < 15 or not np.isfinite(ann) or not np.isfinite(dd):
        return -1e9
    base = 0.55 * (oos_ann if np.isfinite(oos_ann) else ann) + 0.45 * ann
    return float(base + 0.35 * dd)  # dd negative


def main() -> None:
    warnings.filterwarnings("ignore")
    OUT.mkdir(parents=True, exist_ok=True)
    t_all = time.time()

    print("=== Load + extend RT to 2026-07-14 (nowcast fill) ===")
    df0, meta0 = load_aligned()
    df, meta, audit = extend_aligned_to_end(df0, meta0, END_DATE)
    print(
        f"rows={len(df)} {pd.Timestamp(df.trade_date.min()).date()} → {pd.Timestamp(df.trade_date.max()).date()} "
        f"filled={len(audit.get('filled_days') or [])}"
    )
    (OUT / "data_extension_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    rt = df["rt"].to_numpy(dtype=float)
    dd = df["dd60"].to_numpy(dtype=float)
    csi_open = df["csi_open"].to_numpy(dtype=float)
    csi_close = df["csi_close"].to_numpy(dtype=float)
    dates = df["trade_date"]
    n = len(df)

    # Buy&hold benchmark
    bh = df["csi_close"].pct_change().fillna(0.0).to_numpy(dtype=float)
    bh_stats = stats_from_daily(bh)
    print(f"B&H CSI300 ann={bh_stats['ann_return']:.2%} dd={bh_stats['max_dd']:.2%}")

    # ------------------------------------------------------------------
    # A) Dense CORE grid
    # ------------------------------------------------------------------
    print("=== A) Dense CORE signal grid ===")
    rt_lows = list(range(50, 71))  # 21
    rt_highs = list(range(70, 91))  # 21
    dd_maxes = [-0.03, -0.04, -0.05, -0.06, -0.07, -0.08, -0.10]
    core_holds = [3, 4, 5, 6, 7, 8, 10]
    cost_bps_core = [3, 15]  # two cost layers for core grid (dense enough)

    core_rows = []
    t0 = time.time()
    for cost_bps in cost_bps_core:
        buy_cost = sell_cost = cost_bps / 10000.0
        for rt_low, rt_high, dd_max, hold in itertools.product(rt_lows, rt_highs, dd_maxes, core_holds):
            if rt_low >= rt_high:
                continue
            daily, rets = simulate_core_daily(
                rt,
                dd,
                csi_open,
                csi_close,
                rt_low=rt_low,
                rt_high=rt_high,
                dd_max=dd_max,
                hold_days=hold,
                buy_cost=buy_cost,
                sell_cost=sell_cost,
            )
            st = stats_from_daily(daily, rets)
            oos = stats_from_daily(oos_daily(daily, dates), [r for r in rets])  # trade filter approx full
            # better OOS trade filter: re-sim is expensive; use daily OOS only
            oos = stats_from_daily(oos_daily(daily, dates))
            row = {
                "rt_low": rt_low,
                "rt_high": rt_high,
                "dd_max": dd_max,
                "core_hold": hold,
                "cost_bps": cost_bps,
                **{f"full_{k}": v for k, v in st.items()},
                "oos_ann_return": oos["ann_return"],
                "oos_max_dd": oos["max_dd"],
                "oos_sharpe": oos["sharpe"],
                "oos_total_return": oos["total_return"],
            }
            row["score"] = score_row(
                {
                    "ann_return": st["ann_return"],
                    "oos_ann_return": oos["ann_return"],
                    "max_dd": st["max_dd"],
                    "trade_count": st["trade_count"],
                }
            )
            core_rows.append(row)
    core_df = pd.DataFrame(core_rows).sort_values("score", ascending=False)
    core_path = OUT / "grid_core_dense.csv"
    core_df.to_csv(core_path, index=False)
    print(f"core combos={len(core_df)} in {time.time() - t0:.1f}s → {core_path}")

    # production core + top cores for portfolio stage
    prod_core = (60, 80, -0.05, 5)
    top_core_params = [(60, 80, -0.05, 5)]
    for _, r in core_df[core_df["cost_bps"] == 3].head(15).iterrows():
        key = (int(r.rt_low), int(r.rt_high), float(r.dd_max), int(r.core_hold))
        if key not in top_core_params:
            top_core_params.append(key)
        if len(top_core_params) >= 12:
            break

    # ------------------------------------------------------------------
    # B) Dense SAT hold window grid (production stages / merge)
    # ------------------------------------------------------------------
    print("=== B) Dense SAT hold-window grid ===")
    stages_all = precompute_stages(df)
    sat_mins = [2, 3, 4]
    sat_defaults = [3, 4, 5, 6, 7, 8]
    sat_maxes = [5, 6, 7, 8, 10]
    event_flags = [True, False]
    hair_flags = [True, False]
    cost_bps_sat = [3, 15]

    sat_cache: dict[tuple, tuple[np.ndarray, list[float]]] = {}
    sat_rows = []
    t0 = time.time()
    for cost_bps, sat_min, sat_def, sat_max, event_exit, hair in itertools.product(
        cost_bps_sat, sat_mins, sat_defaults, sat_maxes, event_flags, hair_flags
    ):
        if not (sat_min <= sat_def <= sat_max):
            continue
        key = (cost_bps, sat_min, sat_def, sat_max, event_exit, hair)
        buy_cost = sell_cost = cost_bps / 10000.0
        daily, rets = simulate_sat_daily(
            df,
            meta,
            stages_all,
            sat_min=sat_min,
            sat_default=sat_def,
            sat_max=sat_max,
            buy_cost=buy_cost,
            sell_cost=sell_cost,
            apply_haircut=hair,
            event_exit=event_exit,
        )
        sat_cache[key] = (daily, rets)
        st = stats_from_daily(daily, rets)
        oos = stats_from_daily(oos_daily(daily, dates))
        sat_rows.append(
            {
                "cost_bps": cost_bps,
                "sat_min": sat_min,
                "sat_default": sat_def,
                "sat_max": sat_max,
                "event_exit": event_exit,
                "haircut": hair,
                **{f"full_{k}": v for k, v in st.items()},
                "oos_ann_return": oos["ann_return"],
                "oos_max_dd": oos["max_dd"],
                "score": score_row(
                    {
                        "ann_return": st["ann_return"],
                        "oos_ann_return": oos["ann_return"],
                        "max_dd": st["max_dd"],
                        "trade_count": st["trade_count"],
                    }
                ),
            }
        )
    sat_df = pd.DataFrame(sat_rows).sort_values("score", ascending=False)
    sat_path = OUT / "grid_sat_hold_dense.csv"
    sat_df.to_csv(sat_path, index=False)
    print(f"sat combos={len(sat_df)} in {time.time() - t0:.1f}s → {sat_path}")

    # keep production sat + top few for port grid
    prod_sat_key = (3, 3, 5, 8, True, True)
    sat_keys_for_port = [prod_sat_key]
    for _, r in sat_df[sat_df["cost_bps"] == 3].head(8).iterrows():
        k = (
            int(r.cost_bps),
            int(r.sat_min),
            int(r.sat_default),
            int(r.sat_max),
            bool(r.event_exit),
            bool(r.haircut),
        )
        if k not in sat_keys_for_port and k in sat_cache:
            sat_keys_for_port.append(k)
        if len(sat_keys_for_port) >= 6:
            break

    # ------------------------------------------------------------------
    # C) Dense portfolio sizing grid × selected cores × selected sats
    # ------------------------------------------------------------------
    print("=== C) Dense portfolio sizing grid ===")
    w_cores = [i / 100 for i in range(30, 101, 5)]
    w_sats = [i / 100 for i in range(0, 71, 5)]
    total_caps = [0.60, 0.70, 0.80, 0.90, 1.00]
    single_fulls = [False, True]
    cost_bps_port = [3, 15, 30]

    # precompute core dailies for selected cores at each cost
    core_daily_cache: dict[tuple, tuple[np.ndarray, list[float]]] = {}
    for cost_bps in cost_bps_port:
        bc = sc = cost_bps / 10000.0
        for rt_low, rt_high, dd_max, hold in top_core_params:
            key = (cost_bps, rt_low, rt_high, dd_max, hold)
            core_daily_cache[key] = simulate_core_daily(
                rt,
                dd,
                csi_open,
                csi_close,
                rt_low=rt_low,
                rt_high=rt_high,
                dd_max=dd_max,
                hold_days=hold,
                buy_cost=bc,
                sell_cost=sc,
            )

    # ensure sat cache has matching costs for port
    for cost_bps in cost_bps_port:
        for sk in list(sat_keys_for_port):
            # re-key with this cost
            base = (sk[1], sk[2], sk[3], sk[4], sk[5])
            key = (cost_bps, *base)
            if key not in sat_cache:
                sat_cache[key] = simulate_sat_daily(
                    df,
                    meta,
                    stages_all,
                    sat_min=base[0],
                    sat_default=base[1],
                    sat_max=base[2],
                    buy_cost=cost_bps / 10000.0,
                    sell_cost=cost_bps / 10000.0,
                    apply_haircut=base[4],
                    event_exit=base[3],
                )

    port_rows = []
    t0 = time.time()
    combo_n = 0
    for cost_bps in cost_bps_port:
        for rt_low, rt_high, dd_max, hold in top_core_params:
            c_daily, c_rets = core_daily_cache[(cost_bps, rt_low, rt_high, dd_max, hold)]
            for sk in sat_keys_for_port:
                base = (sk[1], sk[2], sk[3], sk[4], sk[5])
                s_daily, s_rets = sat_cache[(cost_bps, *base)]
                for w_c, w_s, cap, single in itertools.product(w_cores, w_sats, total_caps, single_fulls):
                    if w_c + w_s <= 0:
                        continue
                    if w_c + w_s > cap + 1e-9 and not single:
                        # still allow; combine_port will scale
                        pass
                    if w_s == 0 and w_c == 0:
                        continue
                    port = combine_port(
                        c_daily,
                        s_daily,
                        w_core=w_c,
                        w_sat=w_s,
                        total_cap=cap,
                        flex_single_full=single,
                    )
                    st = stats_from_daily(port)
                    oos = stats_from_daily(oos_daily(port, dates))
                    # approximate trade count
                    tc = int((np.asarray(c_rets).size if w_c > 0 else 0) + (np.asarray(s_rets).size if w_s > 0 else 0))
                    st["trade_count"] = tc
                    row = {
                        "cost_bps": cost_bps,
                        "rt_low": rt_low,
                        "rt_high": rt_high,
                        "dd_max": dd_max,
                        "core_hold": hold,
                        "sat_min": base[0],
                        "sat_default": base[1],
                        "sat_max": base[2],
                        "event_exit": base[3],
                        "haircut": base[4],
                        "w_core": w_c,
                        "w_sat": w_s,
                        "total_cap": cap,
                        "flex_single_full": single,
                        **{f"full_{k}": v for k, v in st.items()},
                        "oos_ann_return": oos["ann_return"],
                        "oos_max_dd": oos["max_dd"],
                        "oos_total_return": oos["total_return"],
                        "oos_sharpe": oos["sharpe"],
                        "score": score_row(
                            {
                                "ann_return": st["ann_return"],
                                "oos_ann_return": oos["ann_return"],
                                "max_dd": st["max_dd"],
                                "trade_count": max(tc, st["trade_count"]),
                            }
                        ),
                    }
                    # production mode labels
                    if (
                        (rt_low, rt_high, dd_max, hold) == prod_core
                        and abs(w_c - 0.5) < 1e-9
                        and abs(w_s - 0.3) < 1e-9
                        and abs(cap - 0.8) < 1e-9
                        and single is False
                        and base == (3, 5, 8, True, True)
                        and cost_bps == 3
                    ):
                        row["tag"] = "PROD_CONSERVATIVE"
                    elif (
                        (rt_low, rt_high, dd_max, hold) == prod_core
                        and abs(w_c - 0.6) < 1e-9
                        and abs(w_s - 0.4) < 1e-9
                        and abs(cap - 1.0) < 1e-9
                        and single is True
                        and base == (3, 5, 8, True, True)
                        and cost_bps == 3
                    ):
                        row["tag"] = "PROD_AGGRESSIVE"
                    else:
                        row["tag"] = ""
                    port_rows.append(row)
                    combo_n += 1
    port_df = pd.DataFrame(port_rows).sort_values("score", ascending=False)
    port_path = OUT / "grid_portfolio_dense.csv"
    port_df.to_csv(port_path, index=False)
    print(f"portfolio combos={len(port_df)} in {time.time() - t0:.1f}s → {port_path}")

    # ------------------------------------------------------------------
    # D) Reports
    # ------------------------------------------------------------------
    def pick_tag(tag: str) -> dict | None:
        sub = port_df[port_df["tag"] == tag]
        if sub.empty:
            return None
        return sub.iloc[0].to_dict()

    top20 = port_df.head(20)
    top20_core = core_df[core_df["cost_bps"] == 3].head(20)
    top20_sat = sat_df[sat_df["cost_bps"] == 3].head(20)

    cons = pick_tag("PROD_CONSERVATIVE")
    agg = pick_tag("PROD_AGGRESSIVE")
    # fallback: nearest production-like rows
    if cons is None:
        near = port_df[
            (port_df.cost_bps == 3)
            & (port_df.rt_low == 60)
            & (port_df.rt_high == 80)
            & (port_df.dd_max == -0.05)
            & (port_df.core_hold == 5)
            & (port_df.w_core == 0.5)
            & (port_df.w_sat == 0.3)
        ]
        cons = near.iloc[0].to_dict() if not near.empty else port_df.iloc[0].to_dict()
    if agg is None:
        near = port_df[
            (port_df.cost_bps == 3)
            & (port_df.rt_low == 60)
            & (port_df.rt_high == 80)
            & (port_df.flex_single_full == True)
            & (port_df.w_core == 0.6)
            & (port_df.w_sat == 0.4)
        ]
        agg = near.iloc[0].to_dict() if not near.empty else port_df.iloc[1].to_dict()

    best = port_df.iloc[0].to_dict()

    summary = {
        "title": "Flex dense grid backtest",
        "end_date": str(END_DATE.date()),
        "data_range": {
            "start": str(pd.Timestamp(df.trade_date.min()).date()),
            "end": str(pd.Timestamp(df.trade_date.max()).date()),
            "n": int(n),
        },
        "extension_audit": audit,
        "benchmark_csi300_bh": bh_stats,
        "grid_sizes": {
            "core": int(len(core_df)),
            "satellite_hold": int(len(sat_df)),
            "portfolio": int(len(port_df)),
        },
        "production_conservative": cons,
        "production_aggressive": agg,
        "best_by_score": best,
        "top20_portfolio": top20.to_dict(orient="records"),
        "top20_core_3bps": top20_core.to_dict(orient="records"),
        "top20_sat_3bps": top20_sat.to_dict(orient="records"),
        "elapsed_sec": time.time() - t_all,
        "notes": [
            "Official RT used through last official date; later trading days use nowcast points/bridge.",
            "Core grid: rt_low 50-70, rt_high 70-90, dd 7 levels, hold 7 levels, costs 3/15 bps.",
            "Sat grid: min/default/max holds × event_exit × haircut × costs.",
            "Portfolio grid: top cores × top sats × w_core/w_sat/cap/single × costs 3/15/30.",
            "Score = 0.55*OOS_ann + 0.45*full_ann + 0.35*max_dd (dd is negative).",
            "Research only — not investment advice. Sector indices ≠ live ETFs.",
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Markdown report
    def pct(x):
        try:
            return f"{float(x):.2%}"
        except Exception:
            return "—"

    def md_row(r, cols):
        return "| " + " | ".join(str(r.get(c, "")) for c in cols) + " |"

    lines = [
        "# Flex 最密网格回测报告",
        "",
        f"- 样本：`{summary['data_range']['start']}` → `{summary['data_range']['end']}`（n={summary['data_range']['n']}）",
        f"- 官方 RT 截止：`{audit.get('official_end')}`；缺口填充：`filled={len(audit.get('filled_days') or [])}` 天（nowcast/桥接）",
        f"- 网格规模：核心 **{len(core_df)}** · 卫星持有窗 **{len(sat_df)}** · 组合 **{len(port_df)}**",
        f"- CSI300 买入持有：年化 {pct(bh_stats['ann_return'])} · 最大回撤 {pct(bh_stats['max_dd'])}",
        f"- 耗时：{summary['elapsed_sec']:.1f}s",
        "",
        "## 生产参数对照（cost=3bp）",
        "",
        "| 模式 | 年化 | 最大回撤 | Sharpe | OOS年化 | OOS回撤 | 暴露 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| 保守 prod | {pct(cons.get('full_ann_return'))} | {pct(cons.get('full_max_dd'))} | {cons.get('full_sharpe'):.2f} | {pct(cons.get('oos_ann_return'))} | {pct(cons.get('oos_max_dd'))} | {pct(cons.get('full_exposure_ratio'))} |",
        f"| 进取 prod | {pct(agg.get('full_ann_return'))} | {pct(agg.get('full_max_dd'))} | {agg.get('full_sharpe'):.2f} | {pct(agg.get('oos_ann_return'))} | {pct(agg.get('oos_max_dd'))} | {pct(agg.get('full_exposure_ratio'))} |",
        "",
        "## 全组合网格 Top 15（按 score）",
        "",
        "| rank | score | rt | dd | hold | w_c/w_s | cap | single | sat | cost | ann | dd | oos_ann | trades |",
        "|---:|---:|---|---:|---:|---|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for i, r in top20.head(15).iterrows():
        lines.append(
            "| {rank} | {score:.4f} | {lo}-{hi} | {dd:.0%} | {ch} | {wc:.0%}/{ws:.0%} | {cap:.0%} | {sf} | {smin}-{sdef}-{smax}/{ev}/{hair} | {cb} | {ann} | {mdd} | {oann} | {tc} |".format(
                rank=list(top20.index).index(i) + 1 if False else "",
                score=r.score,
                lo=int(r.rt_low),
                hi=int(r.rt_high),
                dd=float(r.dd_max),
                ch=int(r.core_hold),
                wc=float(r.w_core),
                ws=float(r.w_sat),
                cap=float(r.total_cap),
                sf=bool(r.flex_single_full),
                smin=int(r.sat_min),
                sdef=int(r.sat_default),
                smax=int(r.sat_max),
                ev=bool(r.event_exit),
                hair=bool(r.haircut),
                cb=int(r.cost_bps),
                ann=pct(r.full_ann_return),
                mdd=pct(r.full_max_dd),
                oann=pct(r.oos_ann_return),
                tc=int(r.full_trade_count),
            )
        )
    # fix rank column properly
    lines = [ln for ln in lines if not ln.startswith("| {rank}")]
    # rebuild top table cleanly
    # find marker and rewrite - simpler append new section
    lines.append("")
    lines.append("### Top 15 明细")
    lines.append("")
    lines.append("| # | score | core规则 | hold | 仓位 | cap | single | sat窗 | cost | 年化 | 回撤 | OOS年化 | 交易数 |")
    lines.append("|---:|---:|---|---:|---|---:|---|---|---:|---:|---:|---:|---:|")
    for rank, (_, r) in enumerate(top20.head(15).iterrows(), 1):
        lines.append(
            f"| {rank} | {r.score:.4f} | [{int(r.rt_low)},{int(r.rt_high)}) dd≤{float(r.dd_max):.0%} | {int(r.core_hold)} | "
            f"{float(r.w_core):.0%}/{float(r.w_sat):.0%} | {float(r.total_cap):.0%} | {bool(r.flex_single_full)} | "
            f"{int(r.sat_min)}-{int(r.sat_default)}-{int(r.sat_max)} evt={bool(r.event_exit)} hair={bool(r.haircut)} | "
            f"{int(r.cost_bps)} | {pct(r.full_ann_return)} | {pct(r.full_max_dd)} | {pct(r.oos_ann_return)} | {int(r.full_trade_count)} |"
        )

    lines += [
        "",
        "## 核心规则网格 Top 10（3bp）",
        "",
        "| # | score | rt_low | rt_high | dd_max | hold | 年化 | 回撤 | OOS年化 | 交易数 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, (_, r) in enumerate(top20_core.head(10).iterrows(), 1):
        lines.append(
            f"| {rank} | {r.score:.4f} | {int(r.rt_low)} | {int(r.rt_high)} | {float(r.dd_max):.0%} | {int(r.core_hold)} | "
            f"{pct(r.full_ann_return)} | {pct(r.full_max_dd)} | {pct(r.oos_ann_return)} | {int(r.full_trade_count)} |"
        )

    lines += [
        "",
        "## 卫星持有窗网格 Top 10（3bp）",
        "",
        "| # | score | min | default | max | event | haircut | 年化 | 回撤 | OOS年化 | 交易数 |",
        "|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|",
    ]
    for rank, (_, r) in enumerate(top20_sat.head(10).iterrows(), 1):
        lines.append(
            f"| {rank} | {r.score:.4f} | {int(r.sat_min)} | {int(r.sat_default)} | {int(r.sat_max)} | "
            f"{bool(r.event_exit)} | {bool(r.haircut)} | {pct(r.full_ann_return)} | {pct(r.full_max_dd)} | "
            f"{pct(r.oos_ann_return)} | {int(r.full_trade_count)} |"
        )

    lines += [
        "",
        "## 缺口填充明细",
        "",
    ]
    if audit.get("filled_days"):
        lines.append("| 日期 | RT估算 | 来源 |")
        lines.append("|---|---:|---|")
        for d in audit["filled_days"]:
            lines.append(f"| {d['trade_date']} | {d['rt']:.2f} | {d['source']} |")
    else:
        lines.append(audit.get("note") or "无填充")

    lines += [
        "",
        "## 说明",
        "",
        "1. 正式 RT 优先；官方缺失日至 2026-07-14 用 nowcast 点/线性桥接。",
        "2. 执行假设：T 收盘信号 → T+1 开盘买卖；成本双边 bps。",
        "3. 卫星用申万 L1 / 恒生科技指数代理 ETF；haircut 时 weak 剔除、proxy×0.85。",
        "4. 研究回测，不构成投资建议。",
        "",
    ]
    report_path = OUT / "flex_dense_grid_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print("Wrote", report_path)
    print("=== DONE ===", f"{time.time() - t_all:.1f}s")


if __name__ == "__main__":
    main()
