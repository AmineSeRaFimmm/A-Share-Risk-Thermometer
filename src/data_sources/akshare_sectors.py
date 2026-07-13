from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import os
import time

import pandas as pd

from src.utils.config import load_data_sources
from src.utils.retry import retry_call

os.environ.setdefault("NO_PROXY", "*")


def _sector_symbol(code: object) -> str:
    text = str(code).strip()
    return text.split(".")[0]


def _fetch_one_sector(symbol: str, name: str, fetched_at: str) -> tuple[pd.DataFrame | None, dict[str, object]]:
    import akshare as ak

    try:
        raw = ak.index_hist_sw(symbol=symbol, period="day")
        if raw is None or raw.empty:
            return None, {
                "symbol": symbol,
                "name": name,
                "status": "EMPTY",
                "rows": 0,
                "last_error": "",
                "last_try": fetched_at,
            }
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
        frame = df[[
            "date", "symbol", "name", "open", "close", "high", "low",
            "volume", "amount", "source", "fetch_time",
        ]]
        return frame, {
            "symbol": symbol,
            "name": name,
            "status": "OK",
            "rows": len(frame),
            "last_error": "",
            "last_try": fetched_at,
        }
    except Exception as exc:  # noqa: BLE001
        return None, {
            "symbol": symbol,
            "name": name,
            "status": "ERROR",
            "rows": 0,
            "last_error": str(exc),
            "last_try": fetched_at,
        }


def fetch_sw_level1_sector_history(
    sleep_seconds: float | None = None,
    max_workers: int = 4,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch Shenwan level-1 industry index daily history.

    Returns normalized daily rows and a manifest. Failed sectors are recorded in
    the manifest instead of aborting the whole fetch so cached data can still be
    used by the caller.
    """
    import akshare as ak

    cfg = load_data_sources()
    if sleep_seconds is None:
        sleep_seconds = float(cfg["request_gap_seconds"]) * 0.5
    retry_times = int(cfg["retry_times"])
    retry_sleep = float(cfg["retry_sleep_seconds"])

    fetched_at = datetime.now().isoformat(timespec="seconds")
    info = ak.sw_index_first_info()
    frames: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, object]] = []

    tasks = [
        (_sector_symbol(row["行业代码"]), str(row["行业名称"]))
        for _, row in info.iterrows()
    ]

    def work(item: tuple[str, str]) -> tuple[pd.DataFrame | None, dict[str, object]]:
        symbol, name = item
        return retry_call(
            lambda: _fetch_one_sector(symbol, name, fetched_at),
            times=retry_times,
            sleep_seconds=retry_sleep,
        )

    workers = max(1, min(max_workers, len(tasks) or 1))
    if workers == 1:
        for item in tasks:
            frame, manifest = work(item)
            if frame is not None and not frame.empty:
                frames.append(frame)
            manifest_rows.append(manifest)
            if sleep_seconds:
                time.sleep(sleep_seconds)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(work, item): item for item in tasks}
            for future in as_completed(futures):
                try:
                    frame, manifest = future.result()
                except Exception as exc:  # noqa: BLE001
                    symbol, name = futures[future]
                    frame, manifest = None, {
                        "symbol": symbol,
                        "name": name,
                        "status": "ERROR",
                        "rows": 0,
                        "last_error": str(exc),
                        "last_try": fetched_at,
                    }
                if frame is not None and not frame.empty:
                    frames.append(frame)
                manifest_rows.append(manifest)

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
