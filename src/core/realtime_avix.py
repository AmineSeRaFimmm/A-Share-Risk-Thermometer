from __future__ import annotations
from dataclasses import dataclass
import math
import re
import numpy as np
import pandas as pd

from src.core.avix_formula import _rate_for_dte
from src.core.calendar import get_expiry_date
from src.utils.dates import now_cn

MONTH_RE = re.compile(r"(?i)io(?P<yy>\d{2})(?P<mm>\d{2})")
TARGET_DAYS = 30
MIN_DTE = 7
MAX_DTE = 180
MIN_TERM_OPTIONS = 8
OK_TERM_OPTIONS = 12
MAX_SPREAD_PCT = 0.50
WARN_SPREAD_PCT = 0.30

@dataclass
class RealtimeTermVariance:
    expiry_date: str
    dte: int
    t_year: float
    variance: float
    forward: float
    k0: float
    n_options: int
    n_puts: int
    n_calls: int
    n_valid_pairs: int
    rate: float
    median_spread_pct: float | None
    max_spread_pct: float | None
    strip_min_strike: float | None
    strip_max_strike: float | None
    quality: str
    note: str

def _to_float(value) -> float:
    try:
        out = pd.to_numeric(value, errors="coerce")
        return float(out) if pd.notna(out) else math.nan
    except Exception:
        return math.nan

def _to_int(value) -> int | None:
    try:
        out = pd.to_numeric(value, errors="coerce")
        return int(out) if pd.notna(out) else None
    except Exception:
        return None

def _row_get(row: dict, *names: str):
    for name in names:
        if name in row:
            return row.get(name)
    return None

def _month_from_symbol(month_symbol: str) -> str:
    symbol = str(month_symbol).strip().lower()
    m = MONTH_RE.fullmatch(symbol)
    if not m:
        raise ValueError(f"Unsupported realtime month symbol: {month_symbol}")
    return f"20{m.group('yy')}-{m.group('mm')}"

def infer_realtime_expiry(trade_date: str, trading_days: set) -> str:
    td = pd.to_datetime(trade_date).date()
    month = td.strftime("%Y-%m")
    expiry = get_expiry_date(month, trading_days)
    if (expiry - td).days < MIN_DTE:
        next_month = (pd.Timestamp(td).to_period("M").to_timestamp() + pd.DateOffset(months=1)).strftime("%Y-%m")
        expiry = get_expiry_date(next_month, trading_days)
    return expiry.isoformat()

def _expiry_for_month_symbol(month_symbol: str, trading_days: set) -> tuple[str, str]:
    month = _month_from_symbol(month_symbol)
    expiry = get_expiry_date(month, trading_days)
    return month, expiry.isoformat()

def normalize_realtime_chain(raw: pd.DataFrame, trade_date: str, trading_days: set) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if raw is None or raw.empty:
        return pd.DataFrame()

    td = pd.to_datetime(trade_date).date()
    raw = raw.copy()
    if "month_symbol" not in raw.columns:
        raw["month_symbol"] = "legacy"

    for month_symbol, month_raw in raw.groupby("month_symbol", dropna=False):
        month_symbol = str(month_symbol).lower()
        try:
            if month_symbol == "legacy":
                expiry_date = infer_realtime_expiry(trade_date, trading_days)
                month = expiry_date[:7]
            else:
                month, expiry_date = _expiry_for_month_symbol(month_symbol, trading_days)
        except Exception:
            continue

        expiry = pd.to_datetime(expiry_date).date()
        dte = int((expiry - td).days)
        if dte < MIN_DTE or dte > MAX_DTE:
            continue

        for row in month_raw.to_dict("records"):
            strike = _to_float(_row_get(row, "行权价", "strike"))
            if not np.isfinite(strike) or strike <= 0:
                continue
            pairs = [
                (
                    "C",
                    _row_get(row, "看涨合约-标识", "看涨合约标识", "call_contract"),
                    _row_get(row, "看涨合约-买价", "看涨买价", "call_bid"),
                    _row_get(row, "看涨合约-卖价", "看涨卖价", "call_ask"),
                    _row_get(row, "看涨合约-最新价", "看涨最新价", "call_last"),
                    _row_get(row, "看涨合约-买量", "看涨买量", "call_bid_vol"),
                    _row_get(row, "看涨合约-卖量", "看涨卖量", "call_ask_vol"),
                    _row_get(row, "看涨合约-持仓量", "看涨持仓量", "call_oi"),
                ),
                (
                    "P",
                    _row_get(row, "看跌合约-标识", "看跌合约标识", "put_contract"),
                    _row_get(row, "看跌合约-买价", "看跌买价", "put_bid"),
                    _row_get(row, "看跌合约-卖价", "看跌卖价", "put_ask"),
                    _row_get(row, "看跌合约-最新价", "看跌最新价", "put_last"),
                    _row_get(row, "看跌合约-买量", "看跌买量", "put_bid_vol"),
                    _row_get(row, "看跌合约-卖量", "看跌卖量", "put_ask_vol"),
                    _row_get(row, "看跌合约-持仓量", "看跌持仓量", "put_oi"),
                ),
            ]
            for cp, contract, bid, ask, last, bid_vol, ask_vol, oi in pairs:
                bid_f = _to_float(bid)
                ask_f = _to_float(ask)
                last_f = _to_float(last)
                oi_i = _to_int(oi)
                bid_vol_i = _to_int(bid_vol)
                ask_vol_i = _to_int(ask_vol)
                mid = (bid_f + ask_f) / 2.0 if np.isfinite(bid_f) and np.isfinite(ask_f) else math.nan
                spread_abs = ask_f - bid_f if np.isfinite(bid_f) and np.isfinite(ask_f) else math.nan
                spread_pct = spread_abs / mid if np.isfinite(spread_abs) and np.isfinite(mid) and mid > 0 else math.nan
                reasons: list[str] = []
                if not np.isfinite(bid_f) or bid_f <= 0:
                    reasons.append("BAD_BID")
                if not np.isfinite(ask_f) or ask_f <= 0:
                    reasons.append("BAD_ASK")
                if np.isfinite(bid_f) and np.isfinite(ask_f) and ask_f < bid_f:
                    reasons.append("INVERTED_SPREAD")
                if not np.isfinite(mid) or mid <= 0:
                    reasons.append("BAD_MID")
                if oi_i is None or oi_i <= 0:
                    reasons.append("NO_OPEN_INTEREST")
                if np.isfinite(spread_pct) and spread_pct > MAX_SPREAD_PCT:
                    reasons.append("WIDE_SPREAD")
                valid = len(reasons) == 0
                rows.append({
                    "valuation_time": now_cn().isoformat(timespec="seconds"),
                    "trade_date": trade_date,
                    "month_symbol": month_symbol,
                    "month": month,
                    "contract": str(contract).lower() if contract is not None else f"{month_symbol}{cp}{int(strike)}",
                    "cp": cp,
                    "strike": float(strike),
                    "expiry_date": expiry_date,
                    "dte": dte,
                    "bid": bid_f,
                    "ask": ask_f,
                    "last": last_f,
                    "bid_vol": bid_vol_i,
                    "ask_vol": ask_vol_i,
                    "open_interest": oi_i,
                    "mid": mid,
                    "spread_abs": spread_abs,
                    "spread_pct": spread_pct,
                    "valid_quote": bool(valid),
                    "valid_reason": "OK" if valid else "|".join(reasons),
                    "source": "SINA_AKSHARE_REALTIME_MONTH" if month_symbol != "legacy" else "SINA_AKSHARE_REALTIME_LEGACY",
                })
    return pd.DataFrame(rows)

def _pivot_term(term_chain: pd.DataFrame) -> pd.DataFrame:
    fields = ["bid", "ask", "mid", "open_interest", "bid_vol", "ask_vol", "valid_quote", "spread_pct"]
    available = [col for col in fields if col in term_chain.columns]
    return term_chain.pivot_table(index="strike", columns="cp", values=available, aggfunc="last").sort_index()

def _spread_stats(values: list[float]) -> tuple[float | None, float | None]:
    clean = [float(v) for v in values if np.isfinite(v)]
    if not clean:
        return None, None
    return float(np.median(clean)), float(np.max(clean))

def compute_term_variance(term_chain: pd.DataFrame, rate_curve: pd.DataFrame, trade_date: str) -> RealtimeTermVariance:
    expiry_date = str(term_chain["expiry_date"].iloc[0])
    dte = int(pd.to_numeric(term_chain["dte"], errors="coerce").median())
    t_year = dte / 365.0
    rate, rate_quality = _rate_for_dte(rate_curve, trade_date, dte)
    pivot = _pivot_term(term_chain)

    for col in [("mid", "C"), ("mid", "P"), ("bid", "C"), ("bid", "P"), ("valid_quote", "C"), ("valid_quote", "P")]:
        if col not in pivot.columns:
            raise ValueError(f"{expiry_date} missing required realtime field {col}")

    valid_pair = (
        pivot[("valid_quote", "C")].fillna(False).astype(bool)
        & pivot[("valid_quote", "P")].fillna(False).astype(bool)
        & pd.to_numeric(pivot[("mid", "C")], errors="coerce").gt(0)
        & pd.to_numeric(pivot[("mid", "P")], errors="coerce").gt(0)
    )
    paired = pivot[valid_pair].copy()
    if len(paired) < 3:
        raise ValueError(f"{expiry_date} has too few valid call/put pairs: {len(paired)}")

    paired["abs_cp_diff"] = (paired[("mid", "C")] - paired[("mid", "P")]).abs()
    k_star = float(paired["abs_cp_diff"].idxmin())
    c_star = float(paired.loc[k_star, ("mid", "C")])
    p_star = float(paired.loc[k_star, ("mid", "P")])
    forward = k_star + math.exp(rate * t_year) * (c_star - p_star)

    strikes = np.array(sorted(pivot.index.astype(float)))
    below_forward = strikes[strikes <= forward]
    if len(below_forward) == 0:
        raise ValueError(f"{expiry_date} cannot determine K0")
    k0 = float(below_forward[-1])

    selected: list[dict[str, object]] = []
    notes: list[str] = []
    n_puts = 0
    n_calls = 0
    spread_values: list[float] = []

    try:
        if bool(pivot.loc[k0, ("valid_quote", "C")]) and bool(pivot.loc[k0, ("valid_quote", "P")]):
            q0 = 0.5 * (float(pivot.loc[k0, ("mid", "C")]) + float(pivot.loc[k0, ("mid", "P")]))
            selected.append({"K": k0, "Q": q0, "side": "K0"})
            for side in ["C", "P"]:
                spread = _to_float(pivot.loc[k0, ("spread_pct", side)]) if ("spread_pct", side) in pivot.columns else math.nan
                if np.isfinite(spread):
                    spread_values.append(spread)
        else:
            notes.append("K0_QUOTE_INVALID")
    except Exception:
        notes.append("K0_MISSING")

    zero_count = 0
    for k in sorted([x for x in strikes if x < k0], reverse=True):
        bid = _to_float(pivot.loc[k, ("bid", "P")])
        mid = _to_float(pivot.loc[k, ("mid", "P")])
        valid = bool(pivot.loc[k, ("valid_quote", "P")])
        if not valid or not np.isfinite(bid) or bid <= 0 or not np.isfinite(mid) or mid <= 0:
            if not np.isfinite(bid) or bid <= 0:
                zero_count += 1
            if zero_count >= 2:
                break
            continue
        zero_count = 0
        selected.append({"K": float(k), "Q": mid, "side": "P"})
        n_puts += 1
        spread = _to_float(pivot.loc[k, ("spread_pct", "P")]) if ("spread_pct", "P") in pivot.columns else math.nan
        if np.isfinite(spread):
            spread_values.append(spread)

    zero_count = 0
    for k in sorted([x for x in strikes if x > k0]):
        bid = _to_float(pivot.loc[k, ("bid", "C")])
        mid = _to_float(pivot.loc[k, ("mid", "C")])
        valid = bool(pivot.loc[k, ("valid_quote", "C")])
        if not valid or not np.isfinite(bid) or bid <= 0 or not np.isfinite(mid) or mid <= 0:
            if not np.isfinite(bid) or bid <= 0:
                zero_count += 1
            if zero_count >= 2:
                break
            continue
        zero_count = 0
        selected.append({"K": float(k), "Q": mid, "side": "C"})
        n_calls += 1
        spread = _to_float(pivot.loc[k, ("spread_pct", "C")]) if ("spread_pct", "C") in pivot.columns else math.nan
        if np.isfinite(spread):
            spread_values.append(spread)

    strip = pd.DataFrame(selected).dropna().sort_values("K").reset_index(drop=True)
    if len(strip) < MIN_TERM_OPTIONS:
        raise ValueError(f"{expiry_date} has too few selected OTM options: {len(strip)}")

    ks = strip["K"].to_numpy(dtype=float)
    qs = strip["Q"].to_numpy(dtype=float)
    delta_k = np.empty(len(ks), dtype=float)
    for i in range(len(ks)):
        if i == 0:
            delta_k[i] = ks[i + 1] - ks[i]
        elif i == len(ks) - 1:
            delta_k[i] = ks[i] - ks[i - 1]
        else:
            delta_k[i] = (ks[i + 1] - ks[i - 1]) / 2.0

    contribution = (delta_k / (ks**2)) * math.exp(rate * t_year) * qs
    variance = (2.0 / t_year) * contribution.sum() - (1.0 / t_year) * ((forward / k0 - 1.0) ** 2)
    if not np.isfinite(variance) or variance <= 0:
        raise ValueError(f"{expiry_date} has invalid variance: {variance}")

    median_spread, max_spread = _spread_stats(spread_values)
    quality_flags: list[str] = []
    if len(strip) < OK_TERM_OPTIONS or n_puts < 3 or n_calls < 3:
        quality_flags.append("WARN_FEW_OTM_QUOTES")
    if median_spread is not None and median_spread > WARN_SPREAD_PCT:
        quality_flags.append("WARN_WIDE_MEDIAN_SPREAD")
    if max_spread is not None and max_spread > MAX_SPREAD_PCT:
        quality_flags.append("LOW_WIDE_MAX_SPREAD")
    if "K0_QUOTE_INVALID" in notes or "K0_MISSING" in notes:
        quality_flags.append("WARN_K0_QUOTE")
    if rate_quality != "OK":
        quality_flags.append(rate_quality)

    quality = "|".join(sorted(set(quality_flags))) if quality_flags else "OK"
    return RealtimeTermVariance(
        expiry_date=expiry_date,
        dte=dte,
        t_year=t_year,
        variance=float(variance),
        forward=float(forward),
        k0=float(k0),
        n_options=int(len(strip)),
        n_puts=int(n_puts),
        n_calls=int(n_calls),
        n_valid_pairs=int(len(paired)),
        rate=float(rate),
        median_spread_pct=median_spread,
        max_spread_pct=max_spread,
        strip_min_strike=float(ks.min()) if len(ks) else None,
        strip_max_strike=float(ks.max()) if len(ks) else None,
        quality=quality,
        note=";".join(notes) if notes else "normal",
    )

def _term_quality_flags(*terms: RealtimeTermVariance) -> list[str]:
    flags: list[str] = []
    for item in terms:
        if item.quality != "OK":
            flags.extend(str(item.quality).split("|"))
    return flags

def _build_output(
    trade_date: str,
    valuation_time: str,
    near: RealtimeTermVariance,
    nxt: RealtimeTermVariance,
    var30: float,
    quality_flags: list[str],
    notes: list[str],
    chain: pd.DataFrame,
    fetch_manifest: pd.DataFrame | None,
    close_avix: float | None,
) -> dict[str, object]:
    quality_flags.extend(_term_quality_flags(near, nxt))
    if not np.isfinite(var30) or var30 <= 0:
        quality_flags.append("BAD_NEGATIVE_VAR30")
        avix_mid = math.nan
    else:
        avix_mid = 100.0 * math.sqrt(var30)

    total_quotes = int(len(chain))
    valid_quotes = int(chain["valid_quote"].sum()) if "valid_quote" in chain.columns and not chain.empty else 0
    valid_ratio = valid_quotes / total_quotes if total_quotes else 0.0
    if valid_ratio < 0.45:
        quality_flags.append("WARN_LOW_VALID_QUOTE_RATIO")

    median_spread = pd.to_numeric(chain.loc[chain["valid_quote"], "spread_pct"], errors="coerce").median() if not chain.empty else math.nan
    max_spread = pd.to_numeric(chain.loc[chain["valid_quote"], "spread_pct"], errors="coerce").max() if not chain.empty else math.nan
    if np.isfinite(median_spread) and median_spread > WARN_SPREAD_PCT:
        quality_flags.append("WARN_WIDE_CHAIN_SPREAD")
    if np.isfinite(max_spread) and max_spread > 1.0:
        quality_flags.append("LOW_EXTREME_CHAIN_SPREAD")

    months_fetched = int(chain["month_symbol"].nunique()) if "month_symbol" in chain.columns and not chain.empty else 0
    months_failed = 0
    if fetch_manifest is not None and not fetch_manifest.empty and "status" in fetch_manifest.columns:
        months_failed = int((fetch_manifest["status"].astype(str) != "OK").sum())
    if months_fetched < 2:
        quality_flags.append("LOW_TOO_FEW_MONTHS")
    if months_failed > 0:
        quality_flags.append("WARN_MONTH_FETCH_FAILED")

    if close_avix is not None and np.isfinite(close_avix) and np.isfinite(avix_mid) and close_avix > 0:
        ratio = avix_mid / close_avix
        abs_diff = abs(avix_mid - close_avix)
        if ratio > 2.0 or ratio < 0.35 or abs_diff > 20.0:
            quality_flags.append("BAD_OUTLIER_VS_CLOSE_AVIX")
        elif ratio > 1.5 or ratio < 0.60 or abs_diff > 10.0:
            quality_flags.append("WARN_DEVIATES_FROM_CLOSE_AVIX")

    quality = "|".join(sorted(set(flag for flag in quality_flags if flag and flag != "OK"))) or "OK"
    return {
        "valuation_time": valuation_time,
        "trade_date": trade_date,
        "avix": avix_mid,
        "avix_mid": avix_mid,
        "var30": float(var30) if np.isfinite(var30) else math.nan,
        "near_expiry": near.expiry_date,
        "next_expiry": nxt.expiry_date,
        "near_dte": near.dte,
        "next_dte": nxt.dte,
        "near_var": near.variance,
        "next_var": nxt.variance,
        "near_forward": near.forward,
        "next_forward": nxt.forward,
        "near_k0": near.k0,
        "next_k0": nxt.k0,
        "near_n_options": near.n_options,
        "next_n_options": nxt.n_options,
        "near_n_puts": near.n_puts,
        "next_n_puts": nxt.n_puts,
        "near_n_calls": near.n_calls,
        "next_n_calls": nxt.n_calls,
        "near_rate": near.rate,
        "next_rate": nxt.rate,
        "months_fetched": months_fetched,
        "months_failed": months_failed,
        "total_quotes": total_quotes,
        "valid_quotes": valid_quotes,
        "valid_quote_ratio": valid_ratio,
        "median_spread_pct": float(median_spread) if np.isfinite(median_spread) else math.nan,
        "max_spread_pct": float(max_spread) if np.isfinite(max_spread) else math.nan,
        "near_term_quality": near.quality,
        "next_term_quality": nxt.quality,
        "quality": quality,
        "note": ";".join(notes) if notes else "normal two-term realtime AVIX",
        "source": "SINA_AKSHARE_REALTIME_MONTH_MID",
    }

def compute_realtime_avix_from_chain(
    chain: pd.DataFrame,
    rate_curve: pd.DataFrame,
    trade_date: str,
    fetch_manifest: pd.DataFrame | None = None,
    close_avix: float | None = None,
) -> dict[str, object]:
    valuation_time = now_cn().isoformat(timespec="seconds")
    if chain is None or chain.empty:
        return {
            "valuation_time": valuation_time,
            "trade_date": trade_date,
            "quality": "LOW_NO_REALTIME_CHAIN",
            "source": "SINA_AKSHARE_REALTIME_MONTH_MID",
        }

    day = chain[(chain["trade_date"] == trade_date) & (chain["valid_quote"])].copy()
    if day.empty:
        return {
            "valuation_time": valuation_time,
            "trade_date": trade_date,
            "quality": "LOW_NO_VALID_REALTIME_QUOTES",
            "source": "SINA_AKSHARE_REALTIME_MONTH_MID",
        }

    terms: list[RealtimeTermVariance] = []
    failed_terms: list[str] = []
    for expiry_date, term_chain in day.groupby("expiry_date"):
        try:
            terms.append(compute_term_variance(term_chain, rate_curve, trade_date))
        except Exception as exc:  # noqa: BLE001
            failed_terms.append(f"{expiry_date}:{exc}")

    if not terms:
        return {
            "valuation_time": valuation_time,
            "trade_date": trade_date,
            "quality": "BAD_NO_VALID_REALTIME_TERM",
            "note": ";".join(failed_terms),
            "source": "SINA_AKSHARE_REALTIME_MONTH_MID",
        }

    terms = sorted(terms, key=lambda item: item.dte)
    quality_flags: list[str] = []
    notes: list[str] = []
    if failed_terms:
        quality_flags.append("WARN_TERM_DROPPED")
        notes.extend(failed_terms[:3])

    exact = [item for item in terms if item.dte == TARGET_DAYS]
    if exact:
        near = nxt = exact[0]
        var30 = near.variance
        notes.append("exact 30D term")
    else:
        below = [item for item in terms if item.dte < TARGET_DAYS]
        above = [item for item in terms if item.dte > TARGET_DAYS]
        if below and above:
            near = below[-1]
            nxt = above[0]
            notes.append("normal two-term interpolation")
        elif len(terms) >= 2:
            chosen = sorted(terms, key=lambda item: abs(item.dte - TARGET_DAYS))[:2]
            near, nxt = sorted(chosen, key=lambda item: item.dte)
            quality_flags.append("WARN_NOT_BRACKET_30D")
            notes.append("nearest two terms do not bracket 30D")
        else:
            near = nxt = terms[0]
            var30 = near.variance
            quality_flags.append("LOW_SINGLE_TERM")
            notes.append("single term approximation")
            return _build_output(trade_date, valuation_time, near, nxt, var30, quality_flags, notes, chain, fetch_manifest, close_avix)

        if near.dte == nxt.dte:
            var30 = near.variance
            quality_flags.append("WARN_SAME_DTE")
            notes.append("same DTE terms; using near variance")
        else:
            total_var_target = (
                near.t_year * near.variance * (nxt.dte - TARGET_DAYS) / (nxt.dte - near.dte)
                + nxt.t_year * nxt.variance * (TARGET_DAYS - near.dte) / (nxt.dte - near.dte)
            )
            var30 = total_var_target / (TARGET_DAYS / 365.0)
            if not np.isfinite(var30) or var30 <= 0:
                nearest = sorted(terms, key=lambda item: abs(item.dte - TARGET_DAYS))[0]
                near = nxt = nearest
                var30 = nearest.variance
                quality_flags.append("LOW_VAR30_INTERPOLATION_FAILED")
                notes.append("var30 interpolation failed; nearest term fallback")

    return _build_output(trade_date, valuation_time, near, nxt, var30, quality_flags, notes, chain, fetch_manifest, close_avix)

def calculate_realtime_avix(
    raw: pd.DataFrame,
    rate_curve: pd.DataFrame,
    trade_date: str,
    trading_days: set,
    close_avix: float | None = None,
    fetch_manifest: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if raw is None or raw.empty:
        return pd.DataFrame(), pd.DataFrame([{
            "valuation_time": now_cn().isoformat(timespec="seconds"),
            "trade_date": trade_date,
            "quality": "LOW_NO_REALTIME_CHAIN",
            "source": "SINA_AKSHARE_REALTIME_MONTH_MID",
        }])
    chain = normalize_realtime_chain(raw, trade_date, trading_days)
    result = compute_realtime_avix_from_chain(chain, rate_curve, trade_date, fetch_manifest, close_avix)
    return chain, pd.DataFrame([result])
