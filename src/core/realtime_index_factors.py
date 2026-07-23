from __future__ import annotations

import pandas as pd


def augment_index_history_with_realtime(
    index_history: pd.DataFrame,
    snapshot: pd.DataFrame,
    trade_date: str,
) -> pd.DataFrame:
    """Append valid same-day index quotes without mutating official history."""
    if index_history is None or index_history.empty or snapshot is None or snapshot.empty:
        return index_history.copy() if index_history is not None else pd.DataFrame()
    base = index_history.copy()
    rows = snapshot.copy()
    rows["date"] = str(trade_date)[:10]
    rows["fetch_time"] = rows.get("quote_time")
    for column in ["open", "close", "high", "low", "volume"]:
        if column in rows.columns:
            rows[column] = pd.to_numeric(rows[column], errors="coerce")
    rows = rows.dropna(subset=["symbol", "close"])
    rows = rows[rows["close"] > 0]
    if rows.empty:
        return base
    keep = [column for column in base.columns if column in rows.columns]
    for column in base.columns:
        if column not in rows.columns:
            rows[column] = pd.NA
    rows = rows[base.columns]
    out = pd.concat([base, rows], ignore_index=True)
    return out.drop_duplicates(["date", "symbol"], keep="last").sort_values(["symbol", "date"]).reset_index(drop=True)
