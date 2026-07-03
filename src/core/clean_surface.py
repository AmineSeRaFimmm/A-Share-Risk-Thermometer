from __future__ import annotations
import numpy as np
import pandas as pd
from src.core.avix_formula import implied_vol, black76_price, term_variance

def clean_option_surface(chain: pd.DataFrame, rate_curve: pd.DataFrame) -> pd.DataFrame:
    if chain.empty:
        return chain.copy()
    out = chain.copy()
    out["clean_price"] = out["price_raw"]
    out["clean_valid"] = out["valid_price"] & (out["strike"] > 0) & (out["dte"] >= 7)
    fast_mode = len(out) > 100_000
    for (trade_date, expiry), term in out[out["clean_valid"]].groupby(["trade_date", "expiry_date"]):
        r = 0.02
        rates = rate_curve[pd.to_datetime(rate_curve["trade_date"]) <= pd.to_datetime(trade_date)] if not rate_curve.empty else pd.DataFrame()
        if not rates.empty:
            rates = rates[rates["trade_date"] == rates["trade_date"].max()].sort_values("tenor_days")
            r = float(np.interp(float(term["dte"].median()), rates["tenor_days"], rates["rate"]))
        raw_tv = term_variance(term, "price_raw", r)
        f = raw_tv.get("forward")
        if not f or not np.isfinite(f):
            continue
        idx = term.index
        t = float(term["dte"].median()) / 365
        work = term.copy()
        work["log_moneyness"] = np.log(work["strike"] / f)
        if fast_mode:
            valid = (
                work["price_raw"].gt(0)
                & work["log_moneyness"].abs().le(0.45)
                & pd.to_numeric(work.get("volume", 0), errors="coerce").fillna(0).ge(0)
            )
            out.loc[idx, "clean_valid"] = valid.values
            continue
        work["iv"] = [
            implied_vol(float(row.price_raw), f, float(row.strike), t, r, row.cp)
            for row in work.itertuples()
        ]
        valid = (work["log_moneyness"].abs() <= 0.40) & (work["iv"].between(0.01, 1.5))
        out.loc[idx, "clean_valid"] = valid.values
        clean_iv = work["iv"].where(valid).rolling(5, center=True, min_periods=1).median()
        clean_iv = clean_iv.fillna(work["iv"].where(valid).median())
        prices = []
        for row, sigma in zip(work.itertuples(), clean_iv):
            if np.isfinite(sigma):
                prices.append(black76_price(f, float(row.strike), t, r, float(sigma), row.cp))
            else:
                prices.append(row.price_raw)
        out.loc[idx, "clean_price"] = prices
    return out
