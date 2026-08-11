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

from src.core.data_quality import quality_metadata
from src.utils.config import load_thresholds
from src.utils.retry import retry_call

OPTBBS_K_CSV = "http://1.optbbs.com/d/csv/d/k.csv"
SOURCE_INDEX = "OPTBBS_PARSE_300INDEX_QVIX"
SOURCE_ETF = "OPTBBS_PARSE_300ETF_QVIX_PROXY"
SOURCE_AK_INDEX = "AKSHARE_OPTBBS_QVIX"
SOURCE_AK_ETF = "AKSHARE_OPTBBS_300ETF_QVIX_PROXY"
SOURCE_RT_INDEX_CSV = "OPTBBS_CSV_300INDEX_MIN_QVIX"
SOURCE_RT_INDEX_PAGE = "OPTBBS_PAGE_300INDEX_MIN_QVIX"
SOURCE_RT_INDEX_AK = "AKSHARE_300INDEX_MIN_QVIX"
SOURCE_RT_ETF_CSV = "OPTBBS_CSV_300ETF_MIN_QVIX_PROXY"
SOURCE_RT_ETF_PAGE = "OPTBBS_PAGE_300ETF_MIN_QVIX_PROXY"
SOURCE_RT_ETF_AK = "AKSHARE_300ETF_MIN_QVIX_PROXY"
SOURCE_EOD_CROSS_CONFIRMED = (
    "OPTBBS_300ETF_EOD_QVIX_PROXY_CROSSCHECKED_EASTMONEY_DELAYED"
)

_THRESHOLDS = load_thresholds()
MIN_EOD_ETF_POINTS = int(_THRESHOLDS["min_qvix_eod_etf_points"])
MIN_EOD_INDEX_OPTIONS = int(_THRESHOLDS["min_qvix_eod_index_options"])
MAX_EOD_RELATIVE_DELTA = float(_THRESHOLDS["max_qvix_eod_cross_source_relative_delta"])
EOD_QUOTE_CUTOFF_MINUTE = 14 * 60 + 55

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
    out["source_quote_time"] = out["date"].map(lambda value: f"{value}T15:00:00+08:00")
    out["age_seconds"] = pd.NA
    now_cn = pd.Timestamp.now(tz="Asia/Shanghai")
    today = now_cn.strftime("%Y-%m-%d")
    after_close = (now_cn.hour * 60 + now_cn.minute) >= 15 * 60 + 15
    out["is_final"] = out["date"].map(lambda value: str(value) < today or (str(value) == today and after_close))
    out["is_proxy"] = "PROXY" in source
    out["is_delayed"] = False
    out["sample_size"] = 1
    out["observed"] = True
    out["quality_flags"] = "PROXY" if "PROXY" in source else "OK"
    out = out.dropna(subset=["date"])
    # keep rows with usable close only
    out = out[out["close"].notna() & (out["close"] > 0)].copy()
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _ensure_qvix_metadata(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()
    out = df.copy()
    source = out.get("source", pd.Series("", index=out.index)).fillna("").astype(str)
    dates = pd.to_datetime(out.get("date"), errors="coerce").dt.strftime("%Y-%m-%d")
    proxy = source.str.contains("300ETF|PROXY", case=False, regex=True)
    delayed = source.str.contains("DELAYED", case=False, regex=False)
    now_cn = pd.Timestamp.now(tz="Asia/Shanghai")
    today = now_cn.strftime("%Y-%m-%d")
    after_close = (now_cn.hour * 60 + now_cn.minute) >= 15 * 60 + 15
    final = dates.map(lambda value: bool(pd.notna(value) and (value < today or (value == today and after_close))))
    defaults = {
        "source_quote_time": dates.map(lambda value: f"{value}T15:00:00+08:00" if pd.notna(value) else None),
        "age_seconds": pd.NA,
        "is_final": final,
        "is_proxy": proxy,
        "is_delayed": delayed,
        "sample_size": 1,
        "observed": pd.to_numeric(out.get("close"), errors="coerce").gt(0),
    }
    for column, values in defaults.items():
        if column not in out.columns:
            out[column] = values
        else:
            out[column] = out[column].combine_first(
                values if isinstance(values, pd.Series) else pd.Series(values, index=out.index)
            )
    out["is_final"] = final
    flags = out.get("quality_flags", pd.Series("", index=out.index)).fillna("").astype(str)
    normalized_flags = []
    for current, is_proxy, is_delayed in zip(flags, proxy, delayed):
        parts = [part for part in current.split("|") if part and part != "OK"]
        if is_proxy:
            parts.append("PROXY")
        if is_delayed:
            parts.append("DELAYED")
        normalized_flags.append("|".join(sorted(set(parts))) or "OK")
    out["quality_flags"] = normalized_flags
    return out


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
    last_time = str(out.iloc[-1]["time"])
    now_cn = pd.Timestamp.now(tz="Asia/Shanghai")
    quote = pd.to_datetime(f"{trade_date}T{last_time}", errors="coerce")
    if pd.notna(quote):
        quote = quote.tz_localize("Asia/Shanghai") if quote.tzinfo is None else quote.tz_convert("Asia/Shanghai")
    is_final = bool(
        pd.notna(quote)
        and quote.strftime("%Y-%m-%d") == trade_date
        and (quote.hour * 60 + quote.minute) >= 14 * 60 + 55
        and now_cn.strftime("%Y-%m-%d") >= trade_date
        and (
            now_cn.strftime("%Y-%m-%d") > trade_date
            or (now_cn.hour * 60 + now_cn.minute) >= 15 * 60 + 15
        )
    )
    meta = quality_metadata(
        source=source,
        trade_date=trade_date,
        source_quote_time=last_time,
        fetch_time=datetime.now().astimezone().isoformat(timespec="seconds"),
        sample_size=len(out),
        is_proxy="PROXY" in source,
        is_final=is_final,
        max_age_seconds=15 * 60,
    )
    return pd.DataFrame([{
        "date": trade_date,
        "open": float(out.iloc[0]["qvix"]),
        "high": float(out["qvix"].max()),
        "low": float(out["qvix"].min()),
        "close": float(out.iloc[-1]["qvix"]),
        **meta,
        "last_time": last_time,
        "intraday_points": int(len(out)),
        "qvix_quote_time": meta["source_quote_time"],
        "qvix_delay_minutes": (
            round(float(meta["age_seconds"]) / 60.0, 1)
            if meta["age_seconds"] is not None
            else None
        ),
    }])


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


def _boolean(value) -> bool:
    if value is None or pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _strict_eod_candidate(
    frame: pd.DataFrame,
    trade_date: str,
    *,
    min_samples: int,
    source_fragment: str,
    require_proxy: bool = False,
    require_delayed: bool = False,
) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    row = frame.iloc[-1]
    if str(row.get("date", ""))[:10] != trade_date:
        return None
    source = _text(row.get("source"))
    if source_fragment not in source:
        return None
    close = pd.to_numeric(row.get("close"), errors="coerce")
    sample_size = pd.to_numeric(
        row.get("sample_size", row.get("intraday_points")), errors="coerce"
    )
    quote_text = next(
        (
            text
            for text in [
                _text(row.get("qvix_quote_time")),
                _text(row.get("source_quote_time")),
                _text(row.get("last_time")),
            ]
            if text
        ),
        "",
    )
    quote = pd.to_datetime(quote_text, errors="coerce")
    if pd.notna(quote):
        if quote.tzinfo is None:
            quote = quote.tz_localize("Asia/Shanghai")
        else:
            quote = quote.tz_convert("Asia/Shanghai")
    flags = set(_text(row.get("quality_flags")).split("|"))
    rejected_flags = {"MISSING", "STALE", "TIME_UNVERIFIED", "EMPTY_SAMPLE", "SOURCE_DIVERGENCE"}
    if (
        pd.isna(close)
        or float(close) <= 0
        or pd.isna(sample_size)
        or int(sample_size) < min_samples
        or pd.isna(quote)
        or quote.strftime("%Y-%m-%d") != trade_date
        or quote.hour * 60 + quote.minute < EOD_QUOTE_CUTOFF_MINUTE
        or not _boolean(row.get("observed", True))
        or not _boolean(row.get("is_final"))
        or bool(flags & rejected_flags)
    ):
        return None
    if require_proxy and not (_boolean(row.get("is_proxy")) or "PROXY" in source):
        return None
    if require_delayed and not (_boolean(row.get("is_delayed")) or "DELAYED" in source):
        return None
    return row


def build_cross_confirmed_eod_qvix(
    trade_date: str,
    etf_final: pd.DataFrame,
    eastmoney_final: pd.DataFrame,
) -> pd.DataFrame:
    """Accept an EOD proxy only when independent ETF and index sources agree."""
    trade_date = str(trade_date)[:10]
    etf = _strict_eod_candidate(
        etf_final,
        trade_date,
        min_samples=MIN_EOD_ETF_POINTS,
        source_fragment="300ETF",
        require_proxy=True,
    )
    eastmoney = _strict_eod_candidate(
        eastmoney_final,
        trade_date,
        min_samples=MIN_EOD_INDEX_OPTIONS,
        source_fragment="EASTMONEY_CFFEX_300INDEX",
        require_delayed=True,
    )
    if etf is None or eastmoney is None:
        return pd.DataFrame()

    etf_close = float(pd.to_numeric(etf.get("close"), errors="coerce"))
    eastmoney_close = float(pd.to_numeric(eastmoney.get("close"), errors="coerce"))
    delta = abs(etf_close - eastmoney_close)
    relative_delta = delta / etf_close
    if relative_delta > MAX_EOD_RELATIVE_DELTA:
        print(
            "WARN QVIX EOD cross-check rejected: "
            f"date={trade_date} etf={etf_close:.4f} eastmoney={eastmoney_close:.4f} "
            f"relative_delta={relative_delta:.4f} limit={MAX_EOD_RELATIVE_DELTA:.4f}"
        )
        return pd.DataFrame()

    out = etf.to_frame().T.copy()
    out["source"] = SOURCE_EOD_CROSS_CONFIRMED
    out["is_proxy"] = True
    out["is_delayed"] = True
    out["is_final"] = True
    out["observed"] = True
    out["secondary_source"] = _text(eastmoney.get("source"))
    out["secondary_close"] = eastmoney_close
    out["source_value_delta"] = round(delta, 4)
    out["source_agreement"] = round(max(0.0, 1.0 - relative_delta), 4)
    flags = {
        flag
        for value in [etf.get("quality_flags"), eastmoney.get("quality_flags")]
        for flag in _text(value).split("|")
        if flag and flag != "OK"
    }
    flags.update({"CROSS_CONFIRMED", "DELAYED", "EOD_PROVISIONAL", "PROXY"})
    out["quality_flags"] = "|".join(sorted(flags))
    return _ensure_qvix_metadata(out.reset_index(drop=True))


def _fetch_final_300etf_qvix_for_date(trade_date: str) -> pd.DataFrame:
    sources = [
        lambda: _fetch_optbbs_min_csv(_REALTIME_CSV["300etf"], trade_date, SOURCE_RT_ETF_CSV),
        lambda: _fetch_optbbs_min_from_page(
            _REALTIME_PAGE["300etf"], _REALTIME_CSV["300etf"], trade_date, SOURCE_RT_ETF_PAGE
        ),
        lambda: _fetch_akshare_min_qvix("index_option_300etf_min_qvix", trade_date, SOURCE_RT_ETF_AK),
    ]
    for fetcher in sources:
        try:
            candidate = fetcher()
        except Exception as exc:  # noqa: BLE001
            print(f"WARN 300ETF EOD QVIX source failed {trade_date}: {exc}")
            continue
        if _strict_eod_candidate(
            candidate,
            trade_date,
            min_samples=MIN_EOD_ETF_POINTS,
            source_fragment="300ETF",
            require_proxy=True,
        ) is not None:
            return candidate
    return pd.DataFrame()


def fetch_cross_confirmed_eod_qvix(
    trade_date: str,
    rate_curve: pd.DataFrame,
    index_history: pd.DataFrame,
) -> pd.DataFrame:
    """Fetch and strictly validate a provisional exact-date EOD QVIX proxy."""
    trade_date = str(trade_date)[:10]
    etf = _fetch_final_300etf_qvix_for_date(trade_date)
    if etf.empty:
        return pd.DataFrame()
    from src.data_sources.eastmoney_qvix import fetch_eastmoney_delayed_qvix_for_date

    eastmoney = fetch_eastmoney_delayed_qvix_for_date(trade_date, rate_curve, index_history)
    accepted = build_cross_confirmed_eod_qvix(trade_date, etf, eastmoney)
    if not accepted.empty:
        row = accepted.iloc[0]
        print(
            "QVIX EOD cross-confirmed: "
            f"date={trade_date} close={row.get('close')} secondary={row.get('secondary_close')} "
            f"agreement={row.get('source_agreement')}"
        )
    return accepted


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
        return _ensure_qvix_metadata(fallback)
    if fallback is None or fallback.empty:
        return _ensure_qvix_metadata(primary)
    left = primary.copy()
    right = fallback.copy()
    left["_source_precedence"] = 1
    right["_source_precedence"] = 0
    out = pd.concat([right, left], ignore_index=True, sort=False)
    out["date"] = pd.to_datetime(out.get("date"), errors="coerce").dt.strftime("%Y-%m-%d")
    out["close"] = pd.to_numeric(out.get("close"), errors="coerce")
    out = (
        out.dropna(subset=["date", "close"])
        .loc[lambda df: df["close"].gt(0)]
        .sort_values(["date", "_source_precedence"])
        .drop_duplicates("date", keep="last")
        .drop(columns="_source_precedence")
        .reset_index(drop=True)
    )
    return _ensure_qvix_metadata(out)


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


def fetch_qvix(
    *,
    eod_trade_date: str | None = None,
    rate_curve: pd.DataFrame | None = None,
    index_history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Multi-source QVIX with daily-first, cross-confirmed EOD fallback.

    Order:
      1) Direct parse of optbbs k.csv (index + 300ETF fill)
      2) AKShare 300index / 300etf wrappers if parse path empty
      3) Exact-date 300ETF final minute bar, accepted only when independently
         cross-confirmed by the delayed Eastmoney CFFEX 300-index replica
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
        daily = _ensure_qvix_metadata(merged)
    else:
        print(f"WARN QVIX optbbs parse empty/failed: {meta.get('error', meta)}")
        daily = _ensure_qvix_metadata(_fetch_akshare_qvix_merge())

    target = str(eod_trade_date or "")[:10]
    if not target or rate_curve is None or index_history is None:
        return daily
    daily_dates = set(daily.get("date", pd.Series(dtype=str)).astype(str)) if not daily.empty else set()
    if target in daily_dates:
        return daily
    provisional = fetch_cross_confirmed_eod_qvix(target, rate_curve, index_history)
    return _merge_prefer_primary(daily, provisional)


def _qvix_source_priority(row: pd.Series) -> int:
    source = _text(row.get("source"))
    flags = _text(row.get("quality_flags"))
    if source in {SOURCE_INDEX, SOURCE_AK_INDEX}:
        return 40
    if source in {SOURCE_ETF, SOURCE_AK_ETF}:
        return 30
    if source == SOURCE_EOD_CROSS_CONFIRMED or "EOD_PROVISIONAL" in flags:
        return 20
    if "MIN_QVIX" in source or "DELAYED" in source:
        return 10
    return 0


def merge_qvix_cache(fresh: pd.DataFrame, cached: pd.DataFrame) -> pd.DataFrame:
    """Merge QVIX cache by source authority, then freshness.

    Daily rows always replace provisional EOD rows when they arrive later.
    Provisional rows can never overwrite an existing daily observation.
    """
    if fresh is None or fresh.empty:
        return _ensure_qvix_metadata(cached)
    if cached is None or cached.empty:
        return _ensure_qvix_metadata(fresh)
    old = _ensure_qvix_metadata(cached).copy()
    new = _ensure_qvix_metadata(fresh).copy()
    old["_freshness"] = 0
    new["_freshness"] = 1
    out = pd.concat([old, new], ignore_index=True, sort=False)
    out["date"] = pd.to_datetime(out.get("date"), errors="coerce").dt.strftime("%Y-%m-%d")
    out["close"] = pd.to_numeric(out.get("close"), errors="coerce")
    out = out.dropna(subset=["date", "close"]).loc[lambda df: df["close"].gt(0)].copy()
    out["_source_priority"] = out.apply(_qvix_source_priority, axis=1)
    out = (
        out.sort_values(["date", "_source_priority", "_freshness"])
        .drop_duplicates("date", keep="last")
        .drop(columns=["_source_priority", "_freshness"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    return _ensure_qvix_metadata(out)
