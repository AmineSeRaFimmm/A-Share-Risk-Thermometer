from __future__ import annotations
from datetime import datetime
import re
import pandas as pd
import requests

CONTRACT_RE = re.compile(r"(?i)io(?P<yy>\d{2})(?P<mm>\d{2})(?P<cp>[CP])(?P<strike>\d+)")

def parse_contract(contract: str) -> dict:
    m = CONTRACT_RE.match(contract)
    if not m:
        raise ValueError(f"Unsupported IO contract: {contract}")
    return {
        "month": f"20{m.group('yy')}-{m.group('mm')}",
        "cp": m.group("cp").upper(),
        "strike": int(m.group("strike")),
    }

def fetch_option_daily(symbol: str) -> pd.DataFrame:
    today = datetime.now()
    url = (
        f"https://stock.finance.sina.com.cn/futures/api/jsonp.php/var%20_{symbol}"
        f"{today.year}_{today.month}_{today.day}=/FutureOptionAllService.getOptionDayline"
    )
    response = requests.get(url, params={"symbol": symbol}, timeout=10)
    response.raise_for_status()
    data_text = response.text
    start = data_text.find("[")
    end = data_text.rfind("]")
    if start < 0 or end < start:
        return pd.DataFrame()
    records = eval(data_text[start : end + 1], {"__builtins__": {}})
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame()
    df.columns = ["open", "high", "low", "close", "volume", "date"]
    meta = parse_contract(symbol)
    for col in ["date", "open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = pd.NA
    out = df[["date", "open", "high", "low", "close", "volume"]].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date.astype(str)
    out["contract"] = symbol.lower()
    out["month"] = meta["month"]
    out["cp"] = meta["cp"]
    out["strike"] = meta["strike"]
    out["source"] = "SINA_AKSHARE"
    out["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    return out

def fetch_option_realtime(symbol: str = "io") -> pd.DataFrame:
    import akshare as ak

    df = ak.option_cffex_hs300_spot_sina(symbol=symbol)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["source"] = "SINA_AKSHARE_REALTIME"
    df["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    return df
