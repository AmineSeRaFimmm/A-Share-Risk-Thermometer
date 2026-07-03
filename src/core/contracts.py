from __future__ import annotations
from datetime import date
import pandas as pd
from src.utils.dates import month_iter

def candidate_strikes() -> list[int]:
    strikes = set(range(2500, 7001, 50)) | set(range(2500, 7001, 100))
    return sorted(strikes)

def candidate_contracts(start: date, end: date) -> list[str]:
    rows = []
    for y, m in month_iter(start, end):
        yy = str(y)[-2:]
        mm = f"{m:02d}"
        for strike in candidate_strikes():
            rows.append(f"io{yy}{mm}C{strike}")
            rows.append(f"io{yy}{mm}P{strike}")
    return rows

def build_contract_master(chains: list[pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for df in chains:
        if df.empty:
            continue
        rows.append({
            "contract": str(df["contract"].iloc[0]).lower(),
            "month": df["month"].iloc[0],
            "cp": df["cp"].iloc[0],
            "strike": int(df["strike"].iloc[0]),
            "first_seen_date": df["date"].min(),
            "last_seen_date": df["date"].max(),
            "n_rows": int(len(df)),
            "source": "SINA_AKSHARE",
            "verified": True,
        })
    return pd.DataFrame(rows).drop_duplicates("contract").sort_values(["month", "strike", "cp"]) if rows else pd.DataFrame()
