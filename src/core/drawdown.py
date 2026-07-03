from __future__ import annotations
import pandas as pd
from src.utils.quality import clip

def compute_drawdown(index_history: pd.DataFrame) -> pd.DataFrame:
    if index_history.empty:
        return pd.DataFrame()
    rows = []
    for symbol in ["sh000300", "sh000001"]:
        df = index_history[index_history["symbol"] == symbol].copy().sort_values("date")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df[f"{symbol}_dd60"] = df["close"] / df["close"].rolling(60, min_periods=10).max() - 1
        rows.append(df[["date", "close", f"{symbol}_dd60"]].rename(columns={"date": "trade_date", "close": f"{symbol}_close"}))
    out = rows[0].merge(rows[1], on="trade_date", how="outer").sort_values("trade_date")
    out["hs300_dd_score"] = out["sh000300_dd60"].abs().fillna(0).map(lambda x: clip(x / 0.12 * 100))
    out["sse_dd_score"] = out["sh000001_dd60"].abs().fillna(0).map(lambda x: clip(x / 0.10 * 100))
    out["drawdown_pressure"] = 0.7 * out["hs300_dd_score"] + 0.3 * out["sse_dd_score"]
    return out
