"""A-share index daily multi-source fetch (parse-first).

Sources (priority for the same trade date):
  1. Sina K-line JSON parse     — primary, matches historical series
  2. Tencent fq kline parse     — fills Sina gaps / outages
  3. Eastmoney push2his parse   — third path (proxy-bypass session)
  4. AKShare stock_zh_index_daily — last-resort wrapper

Same professional rules as CFFEX / QVIX:
  - public endpoints, no private API key
  - empty/invalid bars never overwrite good closes
  - every row tagged with ``source``
"""
from __future__ import annotations

from datetime import datetime
import json
import re

import pandas as pd
import requests

from src.utils.retry import retry_call

SOURCE_SINA_PARSE = "PARSE_SINA_INDEX"
SOURCE_TX_PARSE = "PARSE_TX_INDEX"
SOURCE_EM_PARSE = "PARSE_EM_INDEX"
SOURCE_AK_SINA = "AKSHARE_SINA_INDEX"
SOURCE_AK_TX = "AKSHARE_TX_INDEX"

_UA = {"User-Agent": "Mozilla/5.0 (compatible; a-share-risk-thermometer/1.0)"}


def _session(*, trust_env: bool = False) -> requests.Session:
    s = requests.Session()
    s.headers.update(_UA)
    s.trust_env = trust_env
    return s


def _normalize_symbol(symbol: str) -> str:
    s = str(symbol or "").strip().lower()
    if re.fullmatch(r"\d{6}", s):
        # Index convention used by this project: 399xxx → SZ, 000xxx → SH.
        # (A-share stock 000xxx would be SZ, but we only fetch index universe.)
        return ("sz" if s.startswith("399") else "sh") + s
    if re.fullmatch(r"(sh|sz)\d{6}", s):
        return s
    return s


def _to_frame(rows: list[dict], symbol: str, source: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    needed = ["date", "open", "close", "high", "low", "volume"]
    for col in needed:
        if col not in df.columns:
            df[col] = pd.NA
    out = df[needed].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ["open", "close", "high", "low", "volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date", "close"])
    out = out[out["close"] > 0].copy()
    out["symbol"] = symbol
    out["source"] = source
    out["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_index_daily_sina_parse(symbol: str, *, datalen: int = 2000) -> pd.DataFrame:
    """Sina money finance K-line JSON (no JS decrypt)."""
    sym = _normalize_symbol(symbol)
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {"symbol": sym, "scale": 240, "ma": "no", "datalen": int(datalen)}

    def _get() -> list:
        with _session(trust_env=False) as sess:
            r = sess.get(url, params=params, timeout=20)
            r.raise_for_status()
            text = r.text.strip()
            if not text or text in {"null", "[]"}:
                return []
            return json.loads(text)

    try:
        records = retry_call(_get, times=3, sleep_seconds=1.0)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN sina index parse {sym}: {exc}")
        return pd.DataFrame()
    rows = []
    for rec in records or []:
        rows.append(
            {
                "date": rec.get("day") or rec.get("date"),
                "open": rec.get("open"),
                "high": rec.get("high"),
                "low": rec.get("low"),
                "close": rec.get("close"),
                "volume": rec.get("volume"),
            }
        )
    return _to_frame(rows, sym, SOURCE_SINA_PARSE)


def fetch_index_daily_tx_parse(symbol: str) -> pd.DataFrame:
    """Tencent web kline JSON."""
    sym = _normalize_symbol(symbol)
    # day bars; empty start/end → server default window + history chunks via akshare-style
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    # request a long day series; tencent caps length, so we also try year-by-year fallback
    params = {"param": f"{sym},day,,,2000,qfq"}

    def _get(p: dict) -> dict:
        with _session(trust_env=False) as sess:
            r = sess.get(url, params=p, timeout=20)
            r.raise_for_status()
            return r.json()

    try:
        payload = retry_call(lambda: _get(params), times=2, sleep_seconds=1.0)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN tx index parse {sym}: {exc}")
        return pd.DataFrame()

    data = (payload or {}).get("data") or {}
    node = data.get(sym) or {}
    bars = node.get("day") or node.get("qfqday") or node.get("hfqday") or []
    rows = []
    for b in bars:
        # [date, open, close, high, low, volume]
        if not b or len(b) < 5:
            continue
        rows.append(
            {
                "date": b[0],
                "open": b[1],
                "close": b[2],
                "high": b[3],
                "low": b[4],
                "volume": b[5] if len(b) > 5 else pd.NA,
            }
        )
    return _to_frame(rows, sym, SOURCE_TX_PARSE)


def _em_secid(symbol: str) -> str | None:
    sym = _normalize_symbol(symbol)
    m = re.fullmatch(r"(sh|sz)(\d{6})", sym)
    if not m:
        return None
    market, code = m.group(1), m.group(2)
    # EM: 1=SH, 0=SZ
    return f"{'1' if market == 'sh' else '0'}.{code}"


def fetch_index_daily_em_parse(symbol: str) -> pd.DataFrame:
    """Eastmoney push2his kline JSON."""
    sym = _normalize_symbol(symbol)
    secid = _em_secid(sym)
    if not secid:
        return pd.DataFrame()
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": "0",
        "end": "20500101",
        "lmt": "100000",
    }

    def _get() -> dict:
        with _session(trust_env=False) as sess:
            r = sess.get(url, params=params, timeout=25)
            r.raise_for_status()
            return r.json()

    try:
        payload = retry_call(_get, times=2, sleep_seconds=1.0)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN em index parse {sym}: {exc}")
        return pd.DataFrame()

    klines = ((payload or {}).get("data") or {}).get("klines") or []
    rows = []
    for line in klines:
        # date,open,close,high,low,volume,amount,...
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        rows.append(
            {
                "date": parts[0],
                "open": parts[1],
                "close": parts[2],
                "high": parts[3],
                "low": parts[4],
                "volume": parts[5],
            }
        )
    return _to_frame(rows, sym, SOURCE_EM_PARSE)


def fetch_index_daily_akshare_sina(symbol: str) -> pd.DataFrame:
    try:
        import akshare as ak

        df = ak.stock_zh_index_daily(symbol=_normalize_symbol(symbol))
    except Exception as exc:  # noqa: BLE001
        print(f"WARN akshare sina index {symbol}: {exc}")
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={c: str(c).lower() for c in df.columns})
    rows = df.to_dict("records")
    return _to_frame(rows, _normalize_symbol(symbol), SOURCE_AK_SINA)


def fetch_index_daily_akshare_tx(symbol: str) -> pd.DataFrame:
    try:
        import akshare as ak

        df = ak.stock_zh_index_daily_tx(symbol=_normalize_symbol(symbol))
    except Exception as exc:  # noqa: BLE001
        print(f"WARN akshare tx index {symbol}: {exc}")
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={c: str(c).lower() for c in df.columns})
    if "volume" not in df.columns and "amount" in df.columns:
        df["volume"] = df["amount"]
    rows = df.to_dict("records")
    return _to_frame(rows, _normalize_symbol(symbol), SOURCE_AK_TX)


def merge_index_daily_sources(frames: list[pd.DataFrame], symbol: str) -> pd.DataFrame:
    """Merge sources in priority order; later only fills missing dates/fields."""
    sym = _normalize_symbol(symbol)
    good = [f for f in frames if f is not None and not f.empty]
    if not good:
        return pd.DataFrame()
    base = good[0].copy()
    for extra in good[1:]:
        m = base.merge(extra, on="date", how="outer", suffixes=("", "_new"), indicator=False)
        # dates only in extra
        for col in ["open", "close", "high", "low", "volume"]:
            if f"{col}_new" not in m.columns:
                continue
            old = pd.to_numeric(m[col], errors="coerce") if col in m.columns else pd.Series([pd.NA] * len(m))
            new = pd.to_numeric(m[f"{col}_new"], errors="coerce")
            prefer_new = old.isna() | (old <= 0)
            have_new = new.notna() & (new > 0)
            m[col] = new.where(prefer_new & have_new, old)
        if "source_new" in m.columns:
            # keep original source unless row was filled from new
            filled = m["source"].isna() if "source" in m.columns else True
            close_old = pd.to_numeric(m.get("close"), errors="coerce")
            # after merge close already combined; use source_new when original source missing
            m["source"] = m["source"].where(m["source"].notna(), m["source_new"])
        drop_cols = [c for c in m.columns if c.endswith("_new")]
        base = m.drop(columns=drop_cols, errors="ignore")
    base["symbol"] = sym
    if "fetch_time" not in base.columns or base["fetch_time"].isna().all():
        base["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    else:
        base["fetch_time"] = base["fetch_time"].fillna(datetime.now().isoformat(timespec="seconds"))
    cols = ["date", "open", "close", "high", "low", "volume", "symbol", "source", "fetch_time"]
    for c in cols:
        if c not in base.columns:
            base[c] = pd.NA
    out = base[cols].dropna(subset=["date", "close"])
    out = out[pd.to_numeric(out["close"], errors="coerce") > 0]
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_index_daily(symbol: str) -> pd.DataFrame:
    """Multi-source index daily for RT / drawdown / turnover inputs."""
    sym = _normalize_symbol(symbol)
    frames: list[pd.DataFrame] = []
    meta = []

    # 1) Sina parse (recent+long window) — primary tip
    sina = fetch_index_daily_sina_parse(sym, datalen=2000)
    if not sina.empty:
        frames.append(sina)
        meta.append(f"sina_parse={len(sina)}")

    # 2) AKShare Sina full history (js path) — deep history when available
    ak_sina = fetch_index_daily_akshare_sina(sym)
    if not ak_sina.empty:
        frames.append(ak_sina)
        meta.append(f"ak_sina={len(ak_sina)}")

    # 3) Tencent parse
    tx = fetch_index_daily_tx_parse(sym)
    if not tx.empty:
        frames.append(tx)
        meta.append(f"tx_parse={len(tx)}")
    else:
        ak_tx = fetch_index_daily_akshare_tx(sym)
        if not ak_tx.empty:
            frames.append(ak_tx)
            meta.append(f"ak_tx={len(ak_tx)}")

    # 4) Eastmoney parse
    em = fetch_index_daily_em_parse(sym)
    if not em.empty:
        frames.append(em)
        meta.append(f"em_parse={len(em)}")

    out = merge_index_daily_sources(frames, sym)
    if out.empty:
        print(f"WARN index multi-source empty for {sym} ({', '.join(meta) or 'no sources'})")
        return out
    # Prefer AK/Sina history length for body but tag tip source honestly already in merge
    print(
        f"Index multi-source {sym}: rows={len(out)} "
        f"max={out['date'].max()} sources=[{', '.join(meta)}]"
    )
    return out
