from __future__ import annotations
import math
import numpy as np
import pandas as pd
from scipy.stats import norm

def black76_price(f: float, k: float, t: float, r: float, sigma: float, cp: str) -> float:
    if min(f, k, t, sigma) <= 0:
        return np.nan
    d1 = (math.log(f / k) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    disc = math.exp(-r * t)
    if cp == "C":
        return disc * (f * norm.cdf(d1) - k * norm.cdf(d2))
    return disc * (k * norm.cdf(-d2) - f * norm.cdf(-d1))

def implied_vol(price: float, f: float, k: float, t: float, r: float, cp: str) -> float:
    if price <= 0 or min(f, k, t) <= 0:
        return np.nan
    lo, hi = 0.01, 1.5
    for _ in range(60):
        mid = (lo + hi) / 2
        model = black76_price(f, k, t, r, mid, cp)
        if not np.isfinite(model):
            return np.nan
        if model > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2

def _rate_for_dte(rate_curve: pd.DataFrame, trade_date: str, dte: int) -> tuple[float, str]:
    if rate_curve.empty:
        return 0.02, "WARN_RATE_STALE"
    rc = rate_curve[pd.to_datetime(rate_curve["trade_date"]) <= pd.to_datetime(trade_date)].copy()
    if rc.empty:
        return 0.02, "WARN_RATE_STALE"
    last_date = rc["trade_date"].max()
    rc = rc[rc["trade_date"] == last_date].sort_values("tenor_days")
    if rc.empty:
        return 0.02, "WARN_RATE_STALE"
    rate = float(np.interp(dte, rc["tenor_days"].astype(float), rc["rate"].astype(float)))
    stale = (pd.to_datetime(trade_date) - pd.to_datetime(last_date)).days
    return rate, "WARN_RATE_STALE" if stale > 5 else "OK"

def term_variance(term: pd.DataFrame, price_col: str, r: float) -> dict:
    t_days = int(term["dte"].median())
    t = t_days / 365
    pivot = term.pivot_table(index="strike", columns="cp", values=price_col, aggfunc="mean").dropna()
    if len(pivot) < 3:
        return {"quality": "LOW_TOO_FEW_OPTIONS"}
    pivot["diff"] = (pivot["C"] - pivot["P"]).abs()
    k_star = float(pivot["diff"].idxmin())
    c = float(pivot.loc[k_star, "C"])
    p = float(pivot.loc[k_star, "P"])
    fwd = k_star + math.exp(r * t) * (c - p)
    strikes = sorted(float(k) for k in pivot.index if float(k) <= fwd)
    if not strikes:
        return {"quality": "LOW_NO_CHAIN"}
    k0 = max(strikes)
    otm_rows = []
    for strike in sorted(term["strike"].unique()):
        strike = float(strike)
        if strike < k0:
            sub = term[(term["strike"] == strike) & (term["cp"] == "P")]
        elif strike > k0:
            sub = term[(term["strike"] == strike) & (term["cp"] == "C")]
        else:
            sub = term[term["strike"] == strike]
        if not sub.empty:
            q = float(sub[price_col].mean())
            if q > 0:
                otm_rows.append((strike, q))
    if len(otm_rows) < 5:
        return {"quality": "LOW_TOO_FEW_OPTIONS", "n_options": len(otm_rows)}
    ks = [x[0] for x in otm_rows]
    qs = [x[1] for x in otm_rows]
    total = 0.0
    for i, k in enumerate(ks):
        if i == 0:
            dk = ks[i + 1] - k
        elif i == len(ks) - 1:
            dk = k - ks[i - 1]
        else:
            dk = (ks[i + 1] - ks[i - 1]) / 2
        total += dk / (k * k) * math.exp(r * t) * qs[i]
    variance = (2 / t) * total - (1 / t) * ((fwd / k0 - 1) ** 2)
    quality = "OK"
    if variance <= 0:
        quality = "LOW_NEGATIVE_VARIANCE"
    elif len(otm_rows) < 8:
        quality = "WARN_FEW_OPTIONS"
    return {
        "dte": t_days, "t": t, "variance": variance, "n_options": len(otm_rows),
        "forward": fwd, "k0": k0, "quality": quality,
    }

def calculate_avix_for_date(chain: pd.DataFrame, rate_curve: pd.DataFrame, trade_date: str, price_col: str = "price_raw") -> dict:
    day = chain[(chain["trade_date"] == trade_date) & (chain["valid_price"])].copy()
    if day.empty:
        return {"trade_date": trade_date, "quality": "LOW_NO_CHAIN"}
    terms = []
    rate_flags = []
    for expiry, term in day.groupby("expiry_date"):
        r, q = _rate_for_dte(rate_curve, trade_date, int(term["dte"].median()))
        rate_flags.append(q)
        tv = term_variance(term, price_col, r)
        tv["expiry"] = expiry
        if tv.get("variance", -1) > 0:
            terms.append(tv)
    if not terms:
        return {"trade_date": trade_date, "quality": "BAD_NO_TERM"}
    terms = sorted(terms, key=lambda x: x["dte"])
    exact = next((x for x in terms if x["dte"] == 30), None)
    quality = "OK"
    if exact:
        var30 = exact["variance"]
        near = next_ = exact
    else:
        below = [x for x in terms if x["dte"] < 30]
        above = [x for x in terms if x["dte"] > 30]
        if below and above:
            near, next_ = below[-1], above[0]
            var30 = (near["t"] * near["variance"] * (next_["dte"] - 30) / (next_["dte"] - near["dte"]) + next_["t"] * next_["variance"] * (30 - near["dte"]) / (next_["dte"] - near["dte"])) * 365 / 30
        else:
            near = next_ = min(terms, key=lambda x: abs(x["dte"] - 30))
            var30 = near["variance"]
            quality = "WARN_NOT_BRACKET_30D"
    flags = [quality, near.get("quality"), next_.get("quality")] + rate_flags
    if var30 <= 0:
        flags.append("BAD_NEGATIVE_VARIANCE")
        avix = np.nan
    else:
        avix = 100 * math.sqrt(var30)
    return {
        "trade_date": trade_date,
        "avix": avix,
        "near_expiry": near["expiry"],
        "next_expiry": next_["expiry"],
        "near_dte": near["dte"],
        "next_dte": next_["dte"],
        "near_var": near["variance"],
        "next_var": next_["variance"],
        "near_n_options": near["n_options"],
        "next_n_options": next_["n_options"],
        "near_forward": near["forward"],
        "next_forward": next_["forward"],
        "near_k0": near["k0"],
        "next_k0": next_["k0"],
        "quality": "|".join(sorted(set(f for f in flags if f and f != "OK"))) or "OK",
    }
