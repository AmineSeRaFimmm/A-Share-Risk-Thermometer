from __future__ import annotations

from datetime import datetime
import os
import time

import pandas as pd


os.environ.setdefault("NO_PROXY", "*")


def _sector_symbol(code: object) -> str:
    text = str(code).strip()
    return text.split(".")[0]


def fetch_sw_level1_sector_history(sleep_seconds: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch Shenwan level-1 industry index daily history.

    Returns normalized daily rows and a manifest. Failed sectors are recorded in
    the manifest instead of aborting the whole fetch so cached data can still be
    used by the caller.
    """
    import akshare as ak

    fetched_at = datetime.now().isoformat(timespec="seconds")
    info = ak.sw_index_first_info()
    frames: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, object]] = []

    for _, row in info.iterrows():
        code = row["行业代码"]
        name = row["行业名称"]
        symbol = _sector_symbol(code)
        try:
            raw = ak.index_hist_sw(symbol=symbol, period="day")
            if raw is None or raw.empty:
                manifest_rows.append({
                    "symbol": symbol,
                    "name": name,
                    "status": "EMPTY",
                    "rows": 0,
                    "last_error": "",
                    "last_try": fetched_at,
                })
                continue
            df = raw.rename(columns={
                "日期": "date",
                "代码": "symbol",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            }).copy()
            df["symbol"] = symbol
            df["name"] = str(name)
            df["source"] = "AKSHARE_SW_LEVEL1_INDEX"
            df["fetch_time"] = fetched_at
            frames.append(df[[
                "date", "symbol", "name", "open", "close", "high", "low",
                "volume", "amount", "source", "fetch_time",
            ]])
            manifest_rows.append({
                "symbol": symbol,
                "name": name,
                "status": "OK",
                "rows": len(df),
                "last_error": "",
                "last_try": fetched_at,
            })
        except Exception as exc:  # noqa: BLE001
            manifest_rows.append({
                "symbol": symbol,
                "name": name,
                "status": "ERROR",
                "rows": 0,
                "last_error": str(exc),
                "last_try": fetched_at,
            })
        if sleep_seconds:
            time.sleep(sleep_seconds)

    history = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    manifest = pd.DataFrame(manifest_rows)
    return history, manifest


def fetch_sw_level1_sector_valuation() -> pd.DataFrame:
    """Fetch current SW level-1 valuation snapshot.

    AkShare exposes current PE/PB for the index universe, but not a stable
    historical valuation percentile. Downstream research treats this as a
    cross-sectional snapshot only.
    """
    import akshare as ak

    os.environ.setdefault("NO_PROXY", "*")
    info = ak.sw_index_first_info()
    if info.empty:
        return pd.DataFrame()

    out = info.rename(columns={
        "行业代码": "symbol",
        "行业名称": "name",
        "成份个数": "member_count",
        "静态市盈率": "pe_static",
        "TTM(滚动)市盈率": "pe_ttm",
        "市净率": "pb",
        "静态股息率": "dividend_yield",
    }).copy()
    out["symbol"] = out["symbol"].map(_sector_symbol)
    for column in ["member_count", "pe_static", "pe_ttm", "pb", "dividend_yield"]:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    out["source"] = "AKSHARE_SW_LEVEL1_VALUATION"
    out["fetch_time"] = pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")
    return out[[
        "symbol", "name", "member_count", "pe_static", "pe_ttm", "pb",
        "dividend_yield", "source", "fetch_time",
    ]]
