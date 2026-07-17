"""A-share market breadth multi-source fetch (parse-first).

Sources (priority for a trade date summary):
  1. Eastmoney full A-share spot list  → stock-level ratios (OK)
  2. Eastmoney 涨跌分桶 JSON           → lightweight board distribution
  3. Sohu 涨跌停/涨跌家数历史表         → EOD history + same-day board counts
  4. (caller) index proxy              → WARN_BREADTH_PROXY

Same rules as CFFEX / QVIX / index multi-source:
  - public HTTP parse, no private API key
  - sparse/empty snapshots never mark quality OK
  - every summary row carries quality (+ optional source)
"""
from __future__ import annotations

from datetime import datetime
import re

import pandas as pd
import requests

from src.utils.retry import retry_call

EASTMONEY_CLIST_HOSTS = [
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://87.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
]
EASTMONEY_A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
EASTMONEY_FENBU_URL = "https://push2ex.eastmoney.com/getTopicZDFenBu"
SOHU_ZDT_URL = "https://q.stock.sohu.com/cn/zdt.shtml"

SOURCE_EM_SPOT = "PARSE_EM_A_SPOT"
SOURCE_EM_FENBU = "PARSE_EM_ZDFENBU"
SOURCE_SOHU_ZDT = "PARSE_SOHU_ZDT"
SOURCE_AK_EM = "AKSHARE_EASTMONEY_A_SPOT"

_UA = {
    "User-Agent": "Mozilla/5.0 (compatible; a-share-risk-thermometer/1.0)",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_UA)
    s.trust_env = False
    return s


def _fetch_eastmoney_a_spot_direct() -> pd.DataFrame:
    fields = "f12,f14,f2,f3,f6,f8,f10"
    headers = {
        **_UA,
        "Referer": "https://quote.eastmoney.com/center/gridlist.html",
    }
    base_params = {
        "pz": 100,
        "po": 1,
        "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": EASTMONEY_A_SHARE_FS,
        "fields": fields,
    }
    last_error = None
    for url in EASTMONEY_CLIST_HOSTS:
        try:
            session = _session()
            rows = []
            page = 1
            total = None
            while True:
                params = {**base_params, "pn": page}
                try:
                    response = session.get(url, params=params, headers=headers, timeout=12)
                    response.raise_for_status()
                    data = response.json().get("data") or {}
                    page_rows = data.get("diff") or []
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if len(rows) >= 1000:
                        break
                    raise
                if total is None:
                    total = int(data.get("total") or 0)
                if not page_rows:
                    break
                rows.extend(page_rows)
                if total and len(rows) >= total:
                    break
                page += 1
                if page > 80:
                    break
            if len(rows) < 1000:
                continue
            out = pd.DataFrame(rows).rename(
                columns={
                    "f12": "代码",
                    "f14": "名称",
                    "f2": "最新价",
                    "f3": "涨跌幅",
                    "f6": "成交额",
                    "f8": "换手率",
                    "f10": "量比",
                }
            )
            out["fetch_time"] = datetime.now().isoformat(timespec="seconds")
            out["source"] = SOURCE_EM_SPOT
            return out
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if last_error is not None:
        raise last_error
    return pd.DataFrame()


def fetch_a_breadth_snapshot() -> pd.DataFrame:
    """Stock-level A-share snapshot for true breadth (EM multi-host + AKShare)."""
    df = pd.DataFrame()
    try:
        import akshare as ak

        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            df = df.copy()
            df["fetch_time"] = datetime.now().isoformat(timespec="seconds")
            df["source"] = SOURCE_AK_EM
    except Exception:  # noqa: BLE001
        df = pd.DataFrame()
    if df is None or df.empty:
        try:
            df = _fetch_eastmoney_a_spot_direct()
        except Exception as exc:  # noqa: BLE001
            print(f"WARN EM A-spot direct failed: {exc}")
            df = pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    if "source" not in df.columns:
        df["source"] = SOURCE_EM_SPOT
    return df


def summarize_breadth(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame([{"trade_date": trade_date, "quality": "WARN_BREADTH_MISSING"}])
    price_col = next((c for c in df.columns if str(c) in ["最新价", "price", "最新"]), None)
    pct_col = next((c for c in df.columns if str(c) in ["涨跌幅", "pct_chg", "涨跌幅%"]), None)
    amount_col = next((c for c in df.columns if str(c) in ["成交额", "amount"]), None)
    turnover_col = next((c for c in df.columns if str(c) in ["换手率", "turnover"]), None)
    volume_ratio_col = next((c for c in df.columns if str(c) in ["量比", "volume_ratio"]), None)
    if pct_col is None:
        return pd.DataFrame([{"trade_date": trade_date, "quality": "WARN_BREADTH_MISSING"}])
    work = df.copy()
    if price_col:
        valid = pd.to_numeric(work[price_col], errors="coerce") > 0
    else:
        valid = pd.Series(True, index=work.index)
    pct = pd.to_numeric(work[pct_col], errors="coerce")
    valid_count = int(valid.sum())
    # Reject empty/partial snapshots that would look like "OK" with fake 0/1 ratios.
    if valid_count < 1000:
        return pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "valid_count": valid_count,
                    "quality": "WARN_BREADTH_SPARSE" if valid_count > 0 else "WARN_BREADTH_MISSING",
                    "source": str(work.get("source", pd.Series([""])).iloc[0] if "source" in work.columns else ""),
                }
            ]
        )
    denom = max(valid_count, 1)
    src = ""
    if "source" in work.columns and len(work):
        src = str(work["source"].dropna().iloc[0]) if work["source"].notna().any() else SOURCE_EM_SPOT
    out = {
        "trade_date": trade_date,
        "valid_count": valid_count,
        "advancing_count": int(((pct > 0) & valid).sum()),
        "declining_count": int(((pct < 0) & valid).sum()),
        "big_down_count": int(((pct <= -5) & valid).sum()),
        "limit_down_count": int(((pct <= -9.5) & valid).sum()),
        "advancing_ratio": float(((pct > 0) & valid).sum() / denom),
        "decline_ratio": float(((pct < 0) & valid).sum() / denom),
        "big_down_ratio": float(((pct <= -5) & valid).sum() / denom),
        "limit_down_ratio": float(((pct <= -9.5) & valid).sum() / denom),
        "total_amount": float(pd.to_numeric(work[amount_col], errors="coerce").sum()) if amount_col else None,
        "turnover_median": float(pd.to_numeric(work[turnover_col], errors="coerce").median()) if turnover_col else None,
        "volume_ratio_median": float(pd.to_numeric(work[volume_ratio_col], errors="coerce").median())
        if volume_ratio_col
        else None,
        "quality": "OK",
        "source": src or SOURCE_EM_SPOT,
    }
    return pd.DataFrame([out])


def fetch_eastmoney_zdfenbu_summary(trade_date: str) -> pd.DataFrame:
    """Parse Eastmoney 涨跌分桶 board distribution into breadth ratios."""

    def _get() -> dict:
        with _session() as sess:
            r = sess.get(
                EASTMONEY_FENBU_URL,
                params={"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt"},
                headers={**_UA, "Referer": "https://quote.eastmoney.com/ztb/detail"},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()

    try:
        payload = retry_call(_get, times=3, sleep_seconds=1.0)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN EM ZDFenBu failed: {exc}")
        return pd.DataFrame()

    data = (payload or {}).get("data") or {}
    fenbu = data.get("fenbu") or []
    # qdate like 20260717 — only accept when matches requested trade_date (or unknown)
    qdate = data.get("qdate")
    if qdate is not None:
        try:
            q = str(int(qdate))
            if len(q) == 8:
                qd = f"{q[:4]}-{q[4:6]}-{q[6:8]}"
                if qd != str(trade_date)[:10]:
                    # Still usable as latest session distribution; tag date as qdate
                    trade_date = qd
        except Exception:
            pass

    buckets: dict[int, int] = {}
    for item in fenbu:
        if not isinstance(item, dict):
            continue
        for k, v in item.items():
            try:
                buckets[int(float(k))] = int(v)
            except Exception:
                continue
    if not buckets:
        return pd.DataFrame()

    up = sum(v for k, v in buckets.items() if k > 0)
    down = sum(v for k, v in buckets.items() if k < 0)
    flat = int(buckets.get(0, 0))
    big_down = sum(v for k, v in buckets.items() if k <= -5)
    limit_down = sum(v for k, v in buckets.items() if k <= -9)
    total = up + down + flat
    if total < 1000:
        return pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "valid_count": total,
                    "quality": "WARN_BREADTH_SPARSE",
                    "source": SOURCE_EM_FENBU,
                }
            ]
        )
    denom = float(total)
    return pd.DataFrame(
        [
            {
                "trade_date": trade_date,
                "valid_count": total,
                "advancing_count": up,
                "declining_count": down,
                "big_down_count": big_down,
                "limit_down_count": limit_down,
                "advancing_ratio": up / denom,
                "decline_ratio": down / denom,
                "big_down_ratio": big_down / denom,
                "limit_down_ratio": limit_down / denom,
                "quality": "OK",
                "source": SOURCE_EM_FENBU,
            }
        ]
    )


def _parse_sohu_int(x) -> int:
    s = str(x).strip().replace(",", "").replace("--", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def parse_sohu_zdt_html(html: str, *, default_year: int | None = None) -> pd.DataFrame:
    """Parse Sohu 涨跌停历史 table into breadth summary rows."""
    from bs4 import BeautifulSoup

    year = int(default_year or datetime.now().year)
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return pd.DataFrame()
    rows_out = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 14:
            continue
        # data row starts with MM/DD
        if not re.fullmatch(r"\d{1,2}/\d{1,2}", cells[0]):
            continue
        mm, dd = cells[0].split("/")
        trade_date = f"{year}-{int(mm):02d}-{int(dd):02d}"
        # year rollover: if page is viewed in Jan and row is Dec, use year-1
        try:
            if datetime.now().month <= 2 and int(mm) >= 11:
                trade_date = f"{year - 1}-{int(mm):02d}-{int(dd):02d}"
        except Exception:
            pass

        limit_up = _parse_sohu_int(cells[1])
        limit_down = _parse_sohu_int(cells[2])
        sh_up, sh_flat, sh_down = _parse_sohu_int(cells[5]), _parse_sohu_int(cells[6]), _parse_sohu_int(cells[7])
        sz_up, sz_flat, sz_down = _parse_sohu_int(cells[8]), _parse_sohu_int(cells[9]), _parse_sohu_int(cells[10])
        bj_up, bj_flat, bj_down = _parse_sohu_int(cells[11]), _parse_sohu_int(cells[12]), _parse_sohu_int(cells[13])
        advancing = sh_up + sz_up + bj_up
        declining = sh_down + sz_down + bj_down
        flat = sh_flat + sz_flat + bj_flat
        total = advancing + declining + flat
        if total < 500:
            continue
        denom = float(total)
        # Sohu has limit-down counts but not -5% bucket; use limit_down for both
        # extreme legs and tag source so consumers know big_down ≈ limit-down here.
        rows_out.append(
            {
                "trade_date": trade_date,
                "valid_count": total,
                "advancing_count": advancing,
                "declining_count": declining,
                "big_down_count": limit_down,
                "limit_down_count": limit_down,
                "limit_up_count": limit_up,
                "advancing_ratio": advancing / denom,
                "decline_ratio": declining / denom,
                "big_down_ratio": limit_down / denom,
                "limit_down_ratio": limit_down / denom,
                "quality": "OK",
                "source": SOURCE_SOHU_ZDT,
            }
        )
    if not rows_out:
        return pd.DataFrame()
    return pd.DataFrame(rows_out).drop_duplicates("trade_date", keep="first").sort_values("trade_date")


def fetch_sohu_zdt_history(*, save_html: bool = False) -> pd.DataFrame:
    """Download and parse Sohu zdt history page."""

    def _get() -> str:
        with _session() as sess:
            r = sess.get(SOHU_ZDT_URL, timeout=25, headers={**_UA, "Referer": "https://q.stock.sohu.com/"})
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text

    try:
        html = retry_call(_get, times=3, sleep_seconds=1.5)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN Sohu ZDT fetch failed: {exc}")
        return pd.DataFrame()
    if save_html:
        try:
            from src.storage.paths import RAW, ensure_dirs

            ensure_dirs()
            path = RAW / "breadth" / "sohu_zdt_latest.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
        except Exception:
            pass
    return parse_sohu_zdt_html(html)


def _is_good_breadth_summary(summary: pd.DataFrame) -> bool:
    if summary is None or summary.empty:
        return False
    q = str(summary.iloc[0].get("quality", ""))
    return q.startswith("OK")


def fetch_breadth_summary_multi(trade_date: str) -> pd.DataFrame:
    """Resolve one-day breadth summary via multi-source parse stack."""
    day = str(trade_date)[:10]
    # 1) Full A-share spot (best: true stock-level)
    try:
        snap = fetch_a_breadth_snapshot()
        summary = summarize_breadth(snap, day)
        if _is_good_breadth_summary(summary):
            print(
                f"Breadth multi-source {day}: EM/AK spot OK "
                f"n={summary.iloc[0].get('valid_count')} source={summary.iloc[0].get('source')}"
            )
            return summary
        print(f"WARN breadth spot incomplete {day}: {summary.iloc[0].to_dict() if not summary.empty else summary}")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN breadth spot path failed {day}: {exc}")

    # 2) Eastmoney distribution buckets
    fenbu = fetch_eastmoney_zdfenbu_summary(day)
    if _is_good_breadth_summary(fenbu):
        # if fenbu date differs (latest board), only accept exact day match
        if str(fenbu.iloc[0]["trade_date"])[:10] == day:
            print(f"Breadth multi-source {day}: EM fenbu OK n={fenbu.iloc[0].get('valid_count')}")
            return fenbu
        print(f"WARN EM fenbu date mismatch want={day} got={fenbu.iloc[0].get('trade_date')}")

    # 3) Sohu history table (exact day)
    sohu = fetch_sohu_zdt_history()
    if not sohu.empty:
        hit = sohu[sohu["trade_date"].astype(str) == day]
        if not hit.empty and _is_good_breadth_summary(hit):
            print(f"Breadth multi-source {day}: Sohu ZDT OK n={hit.iloc[0].get('valid_count')}")
            return hit.reset_index(drop=True)

    # fail open with missing tag
    return pd.DataFrame([{"trade_date": day, "quality": "WARN_BREADTH_MISSING"}])


def _is_trusted_stock_breadth_row(row: pd.Series) -> bool:
    """Keep only non-proxy OK rows that look like a real full board."""
    q = str(row.get("quality", "") or "")
    if not q.startswith("OK") or "PROXY" in q:
        return False
    src = str(row.get("source", "") or "")
    # Prefer true stock-level sources; allow empty source only if ratios look sane.
    ar = pd.to_numeric(row.get("advancing_ratio"), errors="coerce")
    dr = pd.to_numeric(row.get("decline_ratio"), errors="coerce")
    if pd.isna(ar) or pd.isna(dr):
        return False
    if float(ar) <= 0.02 or float(ar) >= 0.98:
        return False
    vc = pd.to_numeric(row.get("valid_count"), errors="coerce")
    if pd.notna(vc) and float(vc) < 3000:
        return False
    if src and ("SPOT" not in src.upper()) and ("SOHU" not in src.upper()) and ("FENBU" not in src.upper()):
        # unknown source with sane ratios still acceptable
        pass
    return True


def backfill_breadth_history_from_sohu(existing: pd.DataFrame | None = None) -> pd.DataFrame:
    """Merge Sohu ZDT history into breadth_history.

    Trusted stock-level OK rows are kept; PROXY / MISSING / sparse / extreme
    all-up-all-down rows are replaced when Sohu has that date.
    """
    sohu = fetch_sohu_zdt_history(save_html=True)
    if sohu.empty:
        return existing if existing is not None else pd.DataFrame()
    if existing is None or existing.empty:
        print(f"Breadth Sohu backfill: loaded {len(sohu)} days (empty history)")
        return sohu
    ex = existing.copy()
    trusted_mask = ex.apply(_is_trusted_stock_breadth_row, axis=1)
    trusted_dates = set(ex.loc[trusted_mask, "trade_date"].astype(str))
    keep = ex.loc[trusted_mask].copy()
    # Sohu fills everything not already trusted
    add = sohu[~sohu["trade_date"].astype(str).isin(trusted_dates)].copy()
    merged = pd.concat([keep, add], ignore_index=True)
    merged = merged.drop_duplicates("trade_date", keep="last").sort_values("trade_date").reset_index(drop=True)
    print(
        f"Breadth Sohu backfill: sohu_days={len(sohu)} kept_trusted={len(keep)} "
        f"from_sohu={len(add)} history_n={len(merged)}"
    )
    return merged
