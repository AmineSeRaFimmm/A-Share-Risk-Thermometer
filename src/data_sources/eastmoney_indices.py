"""Intraday index snapshots parsed from Eastmoney's public quote endpoint."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import requests


SOURCE_EASTMONEY_INDEX_RT = "EASTMONEY_INDEX_QUOTE_RT"
SOURCE_TENCENT_INDEX_RT = "TENCENT_INDEX_QUOTE_RT"
_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_TENCENT_URL = "https://qt.gtimg.cn/q="
_SYMBOLS = {"sh000300": "1.000300", "sh000001": "1.000001"}
_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f60"


def _parse_index_payload(payload: dict, symbol: str, trade_date: str) -> dict | None:
    data = (payload or {}).get("data") or {}
    price = pd.to_numeric(data.get("f43"), errors="coerce") / 100.0
    previous = pd.to_numeric(data.get("f60"), errors="coerce") / 100.0
    if pd.isna(price) or float(price) <= 0 or pd.isna(previous) or float(previous) <= 0:
        return None
    # Eastmoney reports index volume in lots; project daily index history uses shares.
    volume = pd.to_numeric(data.get("f47"), errors="coerce") * 100.0
    amount = pd.to_numeric(data.get("f48"), errors="coerce")
    return {
        "trade_date": str(trade_date)[:10],
        "symbol": symbol,
        "open": pd.to_numeric(data.get("f46"), errors="coerce") / 100.0,
        "close": float(price),
        "high": pd.to_numeric(data.get("f44"), errors="coerce") / 100.0,
        "low": pd.to_numeric(data.get("f45"), errors="coerce") / 100.0,
        "previous_close": float(previous),
        "volume": float(volume) if pd.notna(volume) and volume > 0 else None,
        "amount": float(amount) if pd.notna(amount) and amount > 0 else None,
        "source": SOURCE_EASTMONEY_INDEX_RT,
        "quote_time": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def fetch_realtime_index_snapshot(trade_date: str, *, timeout: int = 15) -> pd.DataFrame:
    """Fetch same-day HS300 and SSE composite snapshots.

    Empty/invalid responses are rejected so callers retain the official-close
    path instead of silently treating missing intraday data as current.
    """
    rows: list[dict] = []
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for symbol, secid in _SYMBOLS.items():
        try:
            response = requests.get(
                _URL,
                params={"secid": secid, "fields": _FIELDS},
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; a-share-risk-thermometer/1.0)",
                    "Referer": "https://quote.eastmoney.com/center/gridlist.html",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            row = _parse_index_payload(response.json(), symbol, trade_date)
            if row is not None:
                rows.append(row)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN Eastmoney realtime index failed {symbol}: {exc}")
    if len(rows) == len(_SYMBOLS):
        return pd.DataFrame(rows)

    # Tencent's public quote page is a lightweight fallback when Eastmoney
    # closes its push host or returns an empty response.
    try:
        response = requests.get(
            _TENCENT_URL + ",".join(f"s_{symbol}" for symbol in _SYMBOLS),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
            timeout=timeout,
        )
        response.raise_for_status()
        fallback_rows: list[dict] = []
        for line in response.text.splitlines():
            if '="' not in line:
                continue
            symbol = next((s for s in _SYMBOLS if f"v_s_{s}" in line), None)
            if symbol is None:
                continue
            values = line.split('="', 1)[1].rsplit('"', 1)[0].split("~")
            if len(values) < 7:
                continue
            price = pd.to_numeric(values[3], errors="coerce")
            change = pd.to_numeric(values[4], errors="coerce")
            volume = pd.to_numeric(values[6], errors="coerce")
            if pd.isna(price) or pd.isna(change) or float(price) <= 0:
                continue
            fallback_rows.append({
                "trade_date": str(trade_date)[:10],
                "symbol": symbol,
                "open": None,
                "close": float(price),
                "high": None,
                "low": None,
                "previous_close": float(price) - float(change),
                "volume": float(volume) * 100.0 if pd.notna(volume) and volume > 0 else None,
                "amount": None,
                "source": SOURCE_TENCENT_INDEX_RT,
                "quote_time": fetched_at,
            })
        if len(fallback_rows) == len(_SYMBOLS):
            return pd.DataFrame(fallback_rows)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN Tencent realtime index failed: {exc}")
    return pd.DataFrame()
