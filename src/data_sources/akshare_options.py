from __future__ import annotations
from datetime import datetime
import re
import time
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

    if str(symbol).strip().lower() == "io":
        df, _manifest = fetch_option_realtime_months()
        return df

    df = ak.option_cffex_hs300_spot_sina(symbol=symbol)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["month_symbol"] = str(symbol).strip().lower()
    df["source"] = "SINA_AKSHARE_REALTIME"
    df["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    return df

def _extract_realtime_month_symbols(month_payload) -> list[str]:
    if isinstance(month_payload, dict):
        values = list(month_payload.values())
        raw_months = values[0] if values else []
    elif isinstance(month_payload, pd.DataFrame):
        raw_months = month_payload.iloc[:, 0].dropna().astype(str).tolist()
    else:
        raise ValueError(f"Unsupported realtime option month list format: {type(month_payload)}")

    symbols: list[str] = []
    for item in raw_months:
        if isinstance(item, (list, tuple, set)):
            candidates = item
        else:
            candidates = [item]
        for candidate in candidates:
            symbol = str(candidate).strip().lower()
            if not symbol:
                continue
            if re.fullmatch(r"\d{4}", symbol):
                symbol = f"io{symbol}"
            if re.fullmatch(r"io\d{4}", symbol) and symbol not in symbols:
                symbols.append(symbol)
    return symbols

def fetch_option_realtime_months(sleep_seconds: float = 0.15) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch HS300 index option realtime chains month by month.

    The legacy Sina/AKShare aggregate symbol can mix terms without reliable expiry
    metadata. This function keeps the month symbol on every row so realtime AVIX
    can calculate each term's expiry and DTE independently.
    """
    import akshare as ak

    fetched_at = datetime.now().isoformat(timespec="seconds")
    month_payload = ak.option_cffex_hs300_list_sina()
    month_symbols = _extract_realtime_month_symbols(month_payload)
    frames: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, object]] = []

    for month_symbol in month_symbols:
        try:
            raw = ak.option_cffex_hs300_spot_sina(symbol=month_symbol)
            if raw is None or raw.empty:
                manifest_rows.append({
                    "month_symbol": month_symbol,
                    "status": "EMPTY",
                    "last_error": "",
                    "last_try": fetched_at,
                    "rows": 0,
                })
                continue
            df = raw.copy()
            df["month_symbol"] = month_symbol
            df["source"] = "SINA_AKSHARE_REALTIME_MONTH"
            df["fetch_time"] = fetched_at
            frames.append(df)
            manifest_rows.append({
                "month_symbol": month_symbol,
                "status": "OK",
                "last_error": "",
                "last_try": fetched_at,
                "rows": len(df),
            })
        except Exception as exc:  # noqa: BLE001
            manifest_rows.append({
                "month_symbol": month_symbol,
                "status": "ERROR",
                "last_error": str(exc),
                "last_try": fetched_at,
                "rows": 0,
            })
        if sleep_seconds:
            time.sleep(sleep_seconds)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    manifest = pd.DataFrame(manifest_rows)
    return combined, manifest
