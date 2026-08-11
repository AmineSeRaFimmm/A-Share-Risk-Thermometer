from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.special import ndtr

def _black76_prices(
    forward: float | np.ndarray,
    strikes: np.ndarray,
    t: float | np.ndarray,
    rate: float | np.ndarray,
    sigmas: np.ndarray,
    cp: np.ndarray,
) -> np.ndarray:
    """Vectorized Black-76 prices used by the historical surface cleaner."""
    sqrt_t = np.sqrt(t)
    d1 = (np.log(forward / strikes) + 0.5 * sigmas * sigmas * t) / (sigmas * sqrt_t)
    d2 = d1 - sigmas * sqrt_t
    disc = np.exp(-rate * t)
    calls = disc * (forward * ndtr(d1) - strikes * ndtr(d2))
    puts = disc * (strikes * ndtr(-d2) - forward * ndtr(-d1))
    return np.where(cp == "C", calls, puts)


def _implied_vols(
    prices: np.ndarray,
    forward: float | np.ndarray,
    strikes: np.ndarray,
    t: float | np.ndarray,
    rate: float | np.ndarray,
    cp: np.ndarray,
) -> np.ndarray:
    """Vectorized bounded inversion with explicit no-solution rejection."""
    lo = np.full(len(prices), 0.01, dtype=float)
    hi = np.full(len(prices), 1.50, dtype=float)
    floor = _black76_prices(forward, strikes, t, rate, lo, cp)
    ceiling = _black76_prices(forward, strikes, t, rate, hi, cp)
    tolerance = np.maximum(0.01, np.abs(prices) * 1e-4)
    solvable = (
        np.isfinite(prices)
        & np.isfinite(strikes)
        & (prices > 0)
        & (strikes > 0)
        & (prices >= floor - tolerance)
        & (prices <= ceiling + tolerance)
    )
    for _ in range(28):
        mid = (lo + hi) / 2.0
        model = _black76_prices(forward, strikes, t, rate, mid, cp)
        lower = model < prices
        lo = np.where(lower, mid, lo)
        hi = np.where(lower, hi, mid)
    result = (lo + hi) / 2.0
    result[~solvable] = np.nan
    return result


def _rate_lookup(rate_curve: pd.DataFrame):
    if rate_curve.empty:
        return lambda _trade_date, _dte: 0.02
    curve = rate_curve[["trade_date", "tenor_days", "rate"]].copy()
    curve["trade_date"] = pd.to_datetime(curve["trade_date"], errors="coerce")
    curve["tenor_days"] = pd.to_numeric(curve["tenor_days"], errors="coerce")
    curve["rate"] = pd.to_numeric(curve["rate"], errors="coerce")
    curve = curve.dropna().sort_values(["trade_date", "tenor_days"])
    by_date = {
        day: (
            group["tenor_days"].to_numpy(dtype=float),
            group["rate"].to_numpy(dtype=float),
        )
        for day, group in curve.groupby("trade_date", sort=True)
    }
    days = np.asarray(sorted(by_date), dtype="datetime64[ns]")

    def lookup(trade_date: str, dte: float) -> float:
        pos = int(np.searchsorted(days, np.datetime64(trade_date), side="right") - 1)
        if pos < 0:
            return 0.02
        tenors, rates = by_date[pd.Timestamp(days[pos])]
        return float(np.interp(float(dte), tenors, rates))

    return lookup

def clean_option_surface(chain: pd.DataFrame, rate_curve: pd.DataFrame) -> pd.DataFrame:
    if chain.empty:
        return chain.copy()
    out = chain.copy()
    for column in ("strike", "dte", "price_raw"):
        out[column] = pd.to_numeric(out[column], errors="coerce").astype(float)
    out["cp"] = out["cp"].astype(str).astype(object)
    out["trade_date"] = out["trade_date"].astype(str).astype(object)
    out["expiry_date"] = out["expiry_date"].astype(str).astype(object)
    out["_row_id"] = np.arange(len(out), dtype=int)
    out["clean_price"] = out["price_raw"]
    base_valid = out["valid_price"].astype(bool) & (out["strike"] > 0) & (out["dte"] >= 7)
    out["clean_valid"] = False
    rate_for = _rate_lookup(rate_curve)
    keys = ["trade_date", "expiry_date"]
    candidates = out.loc[base_valid]
    paired = candidates.pivot_table(
        index=[*keys, "strike"], columns="cp", values="price_raw", aggfunc="mean"
    )
    if "C" not in paired.columns or "P" not in paired.columns:
        return out.drop(columns="_row_id")
    paired = paired[["C", "P"]].dropna()
    paired["parity_diff"] = (paired["C"] - paired["P"]).abs()
    best_index = paired.groupby(level=[0, 1], sort=False)["parity_diff"].idxmin()
    best = paired.loc[list(best_index)].reset_index()
    dtes = candidates.groupby(keys, as_index=False, sort=False)["dte"].median()
    terms = best.merge(dtes, on=keys, how="inner")
    terms["rate"] = [
        rate_for(str(row.trade_date), float(row.dte)) for row in terms.itertuples()
    ]
    terms["t"] = terms["dte"] / 365.0
    terms["forward"] = terms["strike"] + np.exp(terms["rate"] * terms["t"]) * (terms["C"] - terms["P"])
    terms = terms[keys + ["rate", "t", "forward"]]

    work = out.loc[base_valid].merge(terms, on=keys, how="left", sort=False)
    usable = np.isfinite(work["forward"]) & (work["forward"] > 0)
    work["log_moneyness"] = np.log(work["strike"] / work["forward"])
    work["iv"] = _implied_vols(
        work["price_raw"].to_numpy(dtype=float),
        work["forward"].to_numpy(dtype=float),
        work["strike"].to_numpy(dtype=float),
        work["t"].to_numpy(dtype=float),
        work["rate"].to_numpy(dtype=float),
        work["cp"].to_numpy(),
    )
    valid = usable & work["log_moneyness"].abs().le(0.40) & work["iv"].between(0.01, 1.5)
    work["iv_for_clean"] = work["iv"].where(valid)

    # Calls and puts are separate smiles. The group keys enforce this before
    # the rolling operation, so no alternating C/P row can enter the window.
    work = work.sort_values([*keys, "cp", "strike"])
    group_keys = [*keys, "cp"]
    clean_iv = (
        work.groupby(group_keys, sort=False)["iv_for_clean"]
        .rolling(5, center=True, min_periods=1)
        .median()
        .reset_index(level=group_keys, drop=True)
    )
    group_median = work.groupby(group_keys, sort=False)["iv_for_clean"].transform("median")
    work["clean_iv"] = clean_iv.fillna(group_median)
    valid_sorted = valid.reindex(work.index, fill_value=False)
    finite = np.isfinite(work["clean_iv"]) & valid_sorted
    work.loc[finite, "clean_price"] = _black76_prices(
        work.loc[finite, "forward"].to_numpy(dtype=float),
        work.loc[finite, "strike"].to_numpy(dtype=float),
        work.loc[finite, "t"].to_numpy(dtype=float),
        work.loc[finite, "rate"].to_numpy(dtype=float),
        work.loc[finite, "clean_iv"].to_numpy(dtype=float),
        work.loc[finite, "cp"].to_numpy(),
    )
    work["clean_valid"] = valid_sorted.to_numpy()
    updates = work.set_index("_row_id")[["clean_price", "clean_valid"]]
    out["clean_price"] = out["_row_id"].map(updates["clean_price"]).fillna(out["price_raw"])
    out["clean_valid"] = out["_row_id"].map(updates["clean_valid"]).fillna(False).astype(bool)
    return out.drop(columns="_row_id")
