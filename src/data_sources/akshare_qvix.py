"""QVIX multi-source fetch (parse-first, same spirit as CFFEX RTJ).

Primary upstream is 期权论坛 optbbs public daily CSV (what AKShare wraps):

    http://1.optbbs.com/d/csv/d/k.csv

Column packs in that file (0-based, date = col 0):
  - 50ETF QVIX:   1..4
  - 300ETF QVIX:  9..12
  - 300股指 QVIX: 17..20

Recently the **300 index** pack has been broken upstream (Excel ``#NAME?`` /
empty), while **300ETF** stays populated. For RT confirmation we:

1. Prefer 300 股指 QVIX when close is valid
2. Else fall back to 300ETF QVIX (tagged as proxy source)
3. Optionally try AKShare wrappers if direct CSV fails

Missing QVIX no longer means “single-source total blackout”.
"""
from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from io import StringIO
from urllib.parse import urljoin

import pandas as pd
import requests

from src.utils.retry import retry_call

OPTBBS_K_CSV = "http://1.optbbs.com/d/csv/d/k.csv"
SOURCE_INDEX = "OPTBBS_PARSE_300INDEX_QVIX"
SOURCE_ETF = "OPTBBS_PARSE_300ETF_QVIX"
SOURCE_AK_INDEX = "AKSHARE_OPTBBS_QVIX"
SOURCE_AK_ETF = "AKSHARE_OPTBBS_300ETF_QVIX"
SOURCE_RT_INDEX_CSV = "OPTBBS_CSV_300INDEX_MIN_QVIX"
SOURCE_RT_INDEX_PAGE = "OPTBBS_PAGE_300INDEX_MIN_QVIX"
SOURCE_RT_INDEX_AK = "AKSHARE_300INDEX_MIN_QVIX"
SOURCE_RT_ETF_CSV = "OPTBBS_CSV_300ETF_MIN_QVIX_PROXY"
SOURCE_RT_ETF_PAGE = "OPTBBS_PAGE_300ETF_MIN_QVIX_PROXY"
SOURCE_RT_ETF_AK = "AKSHARE_300ETF_MIN_QVIX_PROXY"

# 0-based OHLC column packs in k.csv (date is always column 0)
_PACKS = {
    "300index": (17, 18, 19, 20),
    "300etf": (9, 10, 11, 12),
}
_REALTIME_CSV = {
    "300index": "http://1.optbbs.com/d/csv/d/vixindex.csv",
    "300etf": "http://1.optbbs.com/d/csv/d/vix300.csv",
}
_REALTIME_PAGE = {
    "300index": "http://1.optbbs.com/d/csv/csvindex.html",
    "300etf": "http://1.optbbs.com/d/csv/csv300.html",
}


def _normalize_ohlc(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "source", "fetch_time"])
    out = df.copy()
    rename = {}
    for c in out.columns:
        s = str(c).lower()
        if c in rename.values():
            continue
        if "date" in s or "日期" in str(c) or str(c) in {"Unnamed: 0", "0"}:
            rename[c] = "date"
        elif s in {"open", "o"} or "开" in str(c):
            rename[c] = "open"
        elif s in {"high", "h"} or "高" in str(c):
            rename[c] = "high"
        elif s in {"low", "l"} or "低" in str(c):
            rename[c] = "low"
        elif s in {"close", "c"} or "收" in str(c):
            rename[c] = "close"
    out = out.rename(columns=rename)
    for col in ["date", "open", "high", "low", "close"]:
        if col not in out.columns:
            out[col] = pd.NA
    out = out[["date", "open", "high", "low", "close"]].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ["open", "high", "low", "close"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
        out.loc[out[col] <= 0, col] = pd.NA
    out["source"] = source
    out["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    out = out.dropna(subset=["date"])
    # keep rows with usable close only
    out = out[out["close"].notna() & (out["close"] > 0)].copy()
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_optbbs_k_csv(*, timeout: int = 30) -> pd.DataFrame:
    """Download raw optbbs daily multi-QVIX table (GBK CSV)."""

    def _get() -> pd.DataFrame:
        resp = requests.get(
            OPTBBS_K_CSV,
            headers={"User-Agent": "Mozilla/5.0 (compatible; a-share-risk-thermometer/1.0)"},
            timeout=timeout,
        )
        resp.raise_for_status()
        # upstream is GBK; fall back if mislabeled
        text = resp.content.decode("gbk", errors="replace")
        return pd.read_csv(StringIO(text))

    return retry_call(_get, times=3, sleep_seconds=1.5)


def _last_modified_trade_date(headers: dict) -> str | None:
    raw = headers.get("Last-Modified") or headers.get("last-modified")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone(pd.Timestamp.now(tz="Asia/Shanghai").tz).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def _normalize_min_qvix(df: pd.DataFrame, trade_date: str, source: str) -> pd.DataFrame:
    if df is None or df.empty or df.shape[1] < 2:
        return pd.DataFrame()
    out = df.iloc[:, :2].copy()
    out.columns = ["time", "qvix"]
    out["qvix"] = pd.to_numeric(out["qvix"], errors="coerce")
    out = out[out["qvix"].notna() & out["qvix"].gt(0)].copy()
    if out.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "date": trade_date,
                "open": float(out.iloc[0]["qvix"]),
                "high": float(out["qvix"].max()),
                "low": float(out["qvix"].min()),
                "close": float(out.iloc[-1]["qvix"]),
                "source": source,
                "fetch_time": datetime.now().isoformat(timespec="seconds"),
                "last_time": str(out.iloc[-1]["time"]),
                "intraday_points": int(len(out)),
            }
        ]
    )


def _fetch_optbbs_min_csv(url: str, trade_date: str, source: str, *, timeout: int = 20) -> pd.DataFrame:
    def _get() -> pd.DataFrame:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; a-share-risk-thermometer/1.0)"},
            timeout=timeout,
        )
        resp.raise_for_status()
        source_date = _last_modified_trade_date(resp.headers)
        if source_date != trade_date:
            print(f"WARN realtime QVIX date mismatch source={source} want={trade_date} got={source_date}")
            return pd.DataFrame()
        raw = pd.read_csv(StringIO(resp.content.decode("utf-8", errors="replace")))
        return _normalize_min_qvix(raw, trade_date, source)

    return retry_call(_get, times=2, sleep_seconds=1.0)


def _fetch_optbbs_min_from_page(page_url: str, expected_csv: str, trade_date: str, source: str) -> pd.DataFrame:
    try:
        resp = requests.get(
            page_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; a-share-risk-thermometer/1.0)"},
            timeout=20,
        )
        resp.raise_for_status()
        text = resp.content.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN realtime QVIX page fetch failed source={source}: {exc}")
        return pd.DataFrame()
    marker = expected_csv.rsplit("/", 1)[-1]
    if marker not in text:
        return pd.DataFrame()
    # The widget uses a relative path such as d/vix300.csv?v=timestamp.
    relative = f"d/{marker}"
    csv_url = urljoin(page_url, relative)
    return _fetch_optbbs_min_csv(csv_url, trade_date, source)


def _fetch_akshare_min_qvix(fn_name: str, trade_date: str, source: str) -> pd.DataFrame:
    today = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d")
    if trade_date != today:
        return pd.DataFrame()
    try:
        import akshare as ak

        fn = getattr(ak, fn_name, None)
        if fn is None:
            return pd.DataFrame()
        return _normalize_min_qvix(fn(), trade_date, source)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN realtime QVIX akshare fetch failed source={source}: {exc}")
        return pd.DataFrame()


def fetch_realtime_qvix_for_date(trade_date: str) -> pd.DataFrame:
    """Fetch exact-date realtime QVIX for estimated close.

    Prefer 300 index realtime QVIX when usable. If it is missing/broken, use
    300ETF realtime QVIX as an explicit proxy. The returned row is intended for
    nowcast only and must not be persisted as official daily QVIX.
    """
    trade_date = str(trade_date)[:10]
    sources = [
        lambda: _fetch_optbbs_min_csv(_REALTIME_CSV["300index"], trade_date, SOURCE_RT_INDEX_CSV),
        lambda: _fetch_optbbs_min_from_page(
            _REALTIME_PAGE["300index"], _REALTIME_CSV["300index"], trade_date, SOURCE_RT_INDEX_PAGE
        ),
        lambda: _fetch_akshare_min_qvix("index_option_300index_min_qvix", trade_date, SOURCE_RT_INDEX_AK),
        lambda: _fetch_optbbs_min_csv(_REALTIME_CSV["300etf"], trade_date, SOURCE_RT_ETF_CSV),
        lambda: _fetch_optbbs_min_from_page(
            _REALTIME_PAGE["300etf"], _REALTIME_CSV["300etf"], trade_date, SOURCE_RT_ETF_PAGE
        ),
        lambda: _fetch_akshare_min_qvix("index_option_300etf_min_qvix", trade_date, SOURCE_RT_ETF_AK),
    ]
    for fetcher in sources:
        try:
            out = fetcher()
        except Exception as exc:  # noqa: BLE001
            print(f"WARN realtime QVIX source failed {trade_date}: {exc}")
            continue
        if out is not None and not out.empty:
            row = out.iloc[0]
            print(
                "QVIX realtime: "
                f"date={trade_date} close={row.get('close')} source={row.get('source')} "
                f"points={row.get('intraday_points')} last_time={row.get('last_time')}"
            )
            return out
    return pd.DataFrame()


def _extract_pack(raw: pd.DataFrame, pack: str, source: str) -> pd.DataFrame:
    cols = _PACKS[pack]
    if raw is None or raw.empty or raw.shape[1] <= max(cols):
        return pd.DataFrame()
    piece = raw.iloc[:, [0, *cols]].copy()
    piece.columns = ["date", "open", "high", "low", "close"]
    return _normalize_ohlc(piece, source)


def fetch_qvix_from_optbbs_parse() -> tuple[pd.DataFrame, dict[str, object]]:
    """Parse optbbs k.csv: prefer 300 index QVIX, fill gaps with 300ETF QVIX."""
    meta: dict[str, object] = {
        "upstream": OPTBBS_K_CSV,
        "index_rows": 0,
        "etf_rows": 0,
        "merged_rows": 0,
        "index_valid_recent": 0,
        "etf_used_as_fallback": 0,
    }
    try:
        raw = fetch_optbbs_k_csv()
    except Exception as exc:  # noqa: BLE001
        meta["error"] = str(exc)[:240]
        return pd.DataFrame(), meta

    idx = _extract_pack(raw, "300index", SOURCE_INDEX)
    etf = _extract_pack(raw, "300etf", SOURCE_ETF)
    meta["index_rows"] = int(len(idx))
    meta["etf_rows"] = int(len(etf))
    if not idx.empty:
        recent = idx.tail(10)
        meta["index_valid_recent"] = int(recent["close"].notna().sum())

    if idx.empty and etf.empty:
        return pd.DataFrame(), meta
    if idx.empty:
        meta["etf_used_as_fallback"] = int(len(etf))
        meta["merged_rows"] = int(len(etf))
        return etf, meta
    if etf.empty:
        meta["merged_rows"] = int(len(idx))
        return idx, meta

    # Outer join by date; prefer index close, else ETF proxy
    left = idx.rename(
        columns={
            "open": "open_i",
            "high": "high_i",
            "low": "low_i",
            "close": "close_i",
            "source": "source_i",
            "fetch_time": "fetch_time_i",
        }
    )
    right = etf.rename(
        columns={
            "open": "open_e",
            "high": "high_e",
            "low": "low_e",
            "close": "close_e",
            "source": "source_e",
            "fetch_time": "fetch_time_e",
        }
    )
    m = left.merge(right, on="date", how="outer")
    use_etf = m["close_i"].isna() & m["close_e"].notna()
    meta["etf_used_as_fallback"] = int(use_etf.sum())
    out = pd.DataFrame({"date": m["date"]})
    for col in ["open", "high", "low", "close"]:
        out[col] = m[f"{col}_i"].combine_first(m[f"{col}_e"])
    out["source"] = m["source_i"].where(~use_etf, m["source_e"])
    out["fetch_time"] = m["fetch_time_i"].combine_first(m["fetch_time_e"])
    out = out.dropna(subset=["date", "close"])
    out = out[out["close"] > 0].sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    meta["merged_rows"] = int(len(out))
    return out, meta


def _fetch_akshare_series(fn_name: str, source: str) -> pd.DataFrame:
    try:
        import akshare as ak

        fn = getattr(ak, fn_name, None)
        if fn is None:
            return pd.DataFrame()
        df = fn()
        return _normalize_ohlc(df, source)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def _merge_prefer_primary(primary: pd.DataFrame, fallback: pd.DataFrame) -> pd.DataFrame:
    """Merge QVIX frames by date, using fallback only where primary has no close."""
    if primary is None or primary.empty:
        return fallback.copy() if fallback is not None and not fallback.empty else pd.DataFrame()
    if fallback is None or fallback.empty:
        return primary.copy()
    cols = ["date", "open", "high", "low", "close", "source", "fetch_time"]
    left = primary.copy()
    right = fallback.copy()
    for frame in (left, right):
        for col in cols:
            if col not in frame.columns:
                frame[col] = pd.NA
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    m = left[cols].merge(right[cols], on="date", how="outer", suffixes=("_p", "_f"))
    primary_close = pd.to_numeric(m["close_p"], errors="coerce")
    fallback_close = pd.to_numeric(m["close_f"], errors="coerce")
    use_fallback = (primary_close.isna() | primary_close.le(0)) & fallback_close.notna() & fallback_close.gt(0)
    out = pd.DataFrame({"date": m["date"]})
    for col in ["open", "high", "low", "close"]:
        primary_col = pd.to_numeric(m[f"{col}_p"], errors="coerce")
        fallback_col = pd.to_numeric(m[f"{col}_f"], errors="coerce")
        out[col] = primary_col.where(~use_fallback, fallback_col)
    out["source"] = m["source_p"].where(~use_fallback, m["source_f"])
    out["fetch_time"] = m["fetch_time_p"].where(~use_fallback, m["fetch_time_f"])
    return (
        out.dropna(subset=["date", "close"])
        .loc[lambda df: pd.to_numeric(df["close"], errors="coerce").gt(0)]
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


def _fetch_akshare_qvix_merge() -> pd.DataFrame:
    idx = _fetch_akshare_series("index_option_300index_qvix", SOURCE_AK_INDEX)
    etf = _fetch_akshare_series("index_option_300etf_qvix", SOURCE_AK_ETF)
    if idx.empty and etf.empty:
        return pd.DataFrame()
    if idx.empty:
        print(f"QVIX multi-source(akshare): using 300ETF only rows={len(etf)}")
        return etf
    if etf.empty:
        print(f"QVIX multi-source(akshare): using 300index only rows={len(idx)}")
        return idx
    m = _merge_prefer_primary(idx, etf)
    fallback_days = int(m["source"].eq(SOURCE_AK_ETF).sum()) if "source" in m.columns else 0
    print(f"QVIX multi-source(akshare merge): rows={len(m)} etf_fallback_days={fallback_days}")
    return m


def fetch_qvix() -> pd.DataFrame:
    """Multi-source QVIX for RT confirmation.

    Order:
      1) Direct parse of optbbs k.csv (index + 300ETF fill)
      2) AKShare 300index / 300etf wrappers if parse path empty
    """
    parsed, meta = fetch_qvix_from_optbbs_parse()
    if not parsed.empty:
        print(
            f"QVIX multi-source(optbbs parse): rows={meta.get('merged_rows')} "
            f"index_rows={meta.get('index_rows')} etf_rows={meta.get('etf_rows')} "
            f"etf_fallback_days={meta.get('etf_used_as_fallback')}"
        )
        ak = _fetch_akshare_qvix_merge()
        merged = _merge_prefer_primary(parsed, ak)
        if not ak.empty and str(ak["date"].max()) > str(parsed["date"].max()):
            print(
                "QVIX multi-source: filled stale optbbs tail "
                f"optbbs_max={parsed['date'].max()} akshare_max={ak['date'].max()} "
                f"merged_rows={len(merged)}"
            )
        return merged

    print(f"WARN QVIX optbbs parse empty/failed: {meta.get('error', meta)}")
    return _fetch_akshare_qvix_merge()


def merge_qvix_cache(fresh: pd.DataFrame, cached: pd.DataFrame) -> pd.DataFrame:
    """Merge fresh QVIX with cache, preferring non-null close values.

    Upstream sometimes returns trailing date rows with empty OHLC; keep prior
    good closes instead of overwriting them with NaN.
    """
    if fresh is None or fresh.empty:
        return cached.copy() if cached is not None and not cached.empty else pd.DataFrame()
    if cached is None or cached.empty:
        return fresh.copy()
    cols = ["date", "open", "high", "low", "close", "source", "fetch_time"]
    for frame in (fresh, cached):
        for col in cols:
            if col not in frame.columns:
                frame[col] = pd.NA
    left = cached[cols].copy()
    right = fresh[cols].copy()
    left["date"] = pd.to_datetime(left["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    right["date"] = pd.to_datetime(right["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    merged = left.merge(right, on="date", how="outer", suffixes=("_old", "_new"))
    out = pd.DataFrame({"date": merged["date"]})
    for col in ["open", "high", "low", "close"]:
        new = pd.to_numeric(merged[f"{col}_new"], errors="coerce")
        old = pd.to_numeric(merged[f"{col}_old"], errors="coerce")
        # Prefer positive new close; else keep old
        prefer_new = new.notna() & (new > 0)
        out[col] = new.where(prefer_new, old)
    # source follows whichever close we kept when possible
    new_close = pd.to_numeric(merged["close_new"], errors="coerce")
    prefer_new = new_close.notna() & (new_close > 0)
    out["source"] = merged["source_new"].where(prefer_new, merged["source_old"])
    out["fetch_time"] = merged["fetch_time_new"].where(prefer_new, merged["fetch_time_old"])
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
