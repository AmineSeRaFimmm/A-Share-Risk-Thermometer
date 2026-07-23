"""Delayed 300-index QVIX replica from Eastmoney's public CFFEX option board.

Eastmoney publishes the CFFEX IO option T-board with best bid/ask quotes, but
the board is delayed by roughly 15 minutes.  This source is therefore only a
nowcast fallback: it must never become an official close observation.
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import requests

from src.core.calendar import merged_trading_days
from src.core.realtime_avix import calculate_realtime_avix


EASTMONEY_CFFEX_OPTION_URL = "https://futsseapi.eastmoney.com/list/variety/option/221/1"
EASTMONEY_TOKEN = "58b2fa8f54638b60b87d69b31969089c"
SOURCE_EASTMONEY_DELAYED = "EASTMONEY_CFFEX_300INDEX_QVIX_DELAYED"
MAX_TERMS = 2


def _decode_jsonp(text: str) -> dict:
    text = str(text or "").strip()
    start = text.find("(")
    end = text.rfind(")")
    payload = text[start + 1 : end] if start >= 0 and end > start else text
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("Eastmoney option payload is not an object")
    return parsed


def _request_board(expiry: str = "", *, timeout: int = 20) -> dict:
    response = requests.get(
        EASTMONEY_CFFEX_OPTION_URL,
        params={
            "token": EASTMONEY_TOKEN,
            "orderBy": "xqj",
            "sort": "asc",
            "cp": "",
            "date": expiry,
            "pageSize": 100,
            "pageIndex": 0,
            "callbackName": "qvix_board",
            "blockName": "txdata",
        },
        headers={"User-Agent": "Mozilla/5.0 (compatible; a-share-risk-thermometer/1.0)"},
        timeout=timeout,
    )
    response.raise_for_status()
    return _decode_jsonp(response.text)


def _level(values, index: int):
    if not isinstance(values, list) or len(values) <= index:
        return None
    return values[index]


def _board_records(payloads: list[dict]) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    rows: list[dict[str, object]] = []
    quote_times: list[pd.Timestamp] = []
    for payload in payloads:
        for pair in payload.get("list", []) or []:
            call = pair.get("callQt") or {}
            put = pair.get("putQt") or {}
            call_symbol = str(call.get("dm") or "").lower()
            put_symbol = str(put.get("dm") or "").lower()
            if not call_symbol.startswith("io") or not put_symbol.startswith("io"):
                continue
            # Eastmoney's 10-level arrays are ask5..ask1,bid1..bid5.
            rows.append({
                "month_symbol": call_symbol.split("-", 1)[0],
                "strike": call.get("xqj"),
                "call_contract": call.get("dm"),
                "call_bid": _level(call.get("mmpjg"), 5),
                "call_ask": _level(call.get("mmpjg"), 4),
                "call_last": call.get("p"),
                "call_bid_vol": _level(call.get("mmpl"), 5),
                "call_ask_vol": _level(call.get("mmpl"), 4),
                "call_oi": call.get("ccl"),
                "put_contract": put.get("dm"),
                "put_bid": _level(put.get("mmpjg"), 5),
                "put_ask": _level(put.get("mmpjg"), 4),
                "put_last": put.get("p"),
                "put_bid_vol": _level(put.get("mmpl"), 5),
                "put_ask_vol": _level(put.get("mmpl"), 4),
                "put_oi": put.get("ccl"),
            })
            for value in [call.get("utime"), put.get("utime")]:
                timestamp = pd.to_datetime(value, unit="s", utc=True, errors="coerce")
                if pd.notna(timestamp):
                    quote_times.append(timestamp)
    return pd.DataFrame(rows), (max(quote_times) if quote_times else None)


def fetch_eastmoney_delayed_qvix_for_date(
    trade_date: str,
    rate_curve: pd.DataFrame,
    index_history: pd.DataFrame,
) -> pd.DataFrame:
    """Return a strict, delayed CFFEX-IO QVIX replica for one trade date.

    The function intentionally returns an empty frame on a bad chain, date
    mismatch, or calculation warning.  Callers may use it only after the
    normal realtime QVIX providers are exhausted.
    """
    trade_date = str(trade_date)[:10]
    if rate_curve is None or rate_curve.empty or index_history is None or index_history.empty:
        return pd.DataFrame()
    try:
        first = _request_board()
        expiries = [str(item.get("date")) for item in first.get("date", []) if item.get("date")]
        payloads = [first]
        for expiry in expiries[1:MAX_TERMS]:
            payloads.append(_request_board(expiry))
        raw, quote_time = _board_records(payloads)
        if raw.empty:
            return pd.DataFrame()
        hs300 = index_history[index_history["symbol"].astype(str) == "sh000300"]
        _chain, result = calculate_realtime_avix(
            raw,
            rate_curve,
            trade_date,
            set(merged_trading_days(hs300)),
            close_avix=None,
        )
        if result.empty:
            return pd.DataFrame()
        calculated = result.iloc[-1]
        value = pd.to_numeric(calculated.get("avix_mid"), errors="coerce")
        if pd.isna(value) or float(value) <= 0 or str(calculated.get("quality")) != "OK":
            return pd.DataFrame()
        quote_time_cn = quote_time.tz_convert("Asia/Shanghai") if quote_time is not None else None
        now_cn = pd.Timestamp.now(tz="Asia/Shanghai")
        delay_minutes = None if quote_time_cn is None else max(0, int((now_cn - quote_time_cn).total_seconds() // 60))
        return pd.DataFrame([{
            "date": trade_date,
            "open": float(value),
            "high": float(value),
            "low": float(value),
            "close": float(value),
            "source": SOURCE_EASTMONEY_DELAYED,
            "fetch_time": datetime.now().isoformat(timespec="seconds"),
            "last_time": quote_time_cn.isoformat(timespec="seconds") if quote_time_cn is not None else None,
            "intraday_points": int(len(raw)),
            "qvix_quote_time": quote_time_cn.isoformat(timespec="seconds") if quote_time_cn is not None else None,
            "qvix_delay_minutes": delay_minutes,
        }])
    except Exception as exc:  # noqa: BLE001
        print(f"WARN Eastmoney delayed QVIX fetch failed {trade_date}: {exc}")
        return pd.DataFrame()
