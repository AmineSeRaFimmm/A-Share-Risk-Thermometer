from __future__ import annotations
import pandas as pd
from src.core.calendar import get_expiry_date
from src.core.avix_formula import calculate_avix_for_date
from src.utils.dates import now_cn

def infer_realtime_expiry(trade_date: str, trading_days: set) -> str:
    td = pd.to_datetime(trade_date).date()
    month = td.strftime("%Y-%m")
    expiry = get_expiry_date(month, trading_days)
    if (expiry - td).days < 7:
        next_month = (pd.Timestamp(td).to_period("M").to_timestamp() + pd.DateOffset(months=1)).strftime("%Y-%m")
        expiry = get_expiry_date(next_month, trading_days)
    return expiry.isoformat()

def normalize_realtime_chain(raw: pd.DataFrame, trade_date: str, expiry_date: str) -> pd.DataFrame:
    rows = []
    if raw.empty:
        return pd.DataFrame()
    expiry = pd.to_datetime(expiry_date).date()
    td = pd.to_datetime(trade_date).date()
    dte = (expiry - td).days
    for row in raw.to_dict("records"):
        strike = pd.to_numeric(row.get("行权价"), errors="coerce")
        if pd.isna(strike) or strike <= 0:
            continue
        for cp, prefix in [("C", "看涨合约"), ("P", "看跌合约")]:
            bid = pd.to_numeric(row.get(f"{prefix}-买价"), errors="coerce")
            ask = pd.to_numeric(row.get(f"{prefix}-卖价"), errors="coerce")
            last = pd.to_numeric(row.get(f"{prefix}-最新价"), errors="coerce")
            oi = pd.to_numeric(row.get(f"{prefix}-持仓量"), errors="coerce")
            ident = row.get(f"{prefix}-标识")
            valid = pd.notna(bid) and pd.notna(ask) and bid > 0 and ask > 0 and ask >= bid and pd.notna(oi) and oi > 0
            mid = (float(bid) + float(ask)) / 2 if valid else None
            rows.append({
                "trade_date": trade_date,
                "contract": str(ident).lower() if ident is not None else f"realtime{cp}{int(strike)}",
                "month": expiry_date[:7],
                "cp": cp,
                "strike": float(strike),
                "expiry_date": expiry_date,
                "dte": dte,
                "bid": bid,
                "ask": ask,
                "last": last,
                "open_interest": oi,
                "mid": mid,
                "valid_price": bool(valid),
                "source": "SINA_AKSHARE_REALTIME",
            })
    return pd.DataFrame(rows)

def calculate_realtime_avix(raw: pd.DataFrame, rate_curve: pd.DataFrame, trade_date: str, trading_days: set) -> tuple[pd.DataFrame, pd.DataFrame]:
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame([{
            "valuation_time": now_cn().isoformat(timespec="seconds"),
            "trade_date": trade_date,
            "quality": "LOW_NO_REALTIME_CHAIN",
            "source": "SINA_AKSHARE_REALTIME",
        }])
    expiry_date = infer_realtime_expiry(trade_date, trading_days)
    chain = normalize_realtime_chain(raw, trade_date, expiry_date)
    result = calculate_avix_for_date(chain, rate_curve, trade_date, "mid") if not chain.empty else {"trade_date": trade_date, "quality": "LOW_NO_REALTIME_CHAIN"}
    row = {
        "valuation_time": now_cn().isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "avix_mid": result.get("avix"),
        "near_expiry": result.get("near_expiry"),
        "next_expiry": result.get("next_expiry"),
        "near_dte": result.get("near_dte"),
        "next_dte": result.get("next_dte"),
        "near_n_options": result.get("near_n_options"),
        "next_n_options": result.get("next_n_options"),
        "quality": result.get("quality", "LOW_NO_REALTIME_CHAIN"),
        "source": "SINA_AKSHARE_REALTIME",
    }
    return chain, pd.DataFrame([row])
