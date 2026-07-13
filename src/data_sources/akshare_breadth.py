from __future__ import annotations
from datetime import datetime
import pandas as pd

EASTMONEY_CLIST_HOSTS = [
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://87.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
]

EASTMONEY_A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def _fetch_eastmoney_a_spot_direct() -> pd.DataFrame:
    import requests

    fields = "f12,f14,f2,f3,f6,f8,f10"
    headers = {
        "User-Agent": "Mozilla/5.0",
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
            session = requests.Session()
            session.trust_env = False
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
            out = pd.DataFrame(rows).rename(columns={
                "f12": "代码",
                "f14": "名称",
                "f2": "最新价",
                "f3": "涨跌幅",
                "f6": "成交额",
                "f8": "换手率",
                "f10": "量比",
            })
            out["fetch_time"] = datetime.now().isoformat(timespec="seconds")
            out["source"] = "EASTMONEY_DIRECT_A_SPOT"
            return out
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if last_error is not None:
        raise last_error
    return pd.DataFrame()


def fetch_a_breadth_snapshot() -> pd.DataFrame:
    try:
        import akshare as ak

        df = ak.stock_zh_a_spot_em()
    except Exception:
        df = pd.DataFrame()
    if df is None or df.empty:
        df = _fetch_eastmoney_a_spot_direct()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["fetch_time"] = datetime.now().isoformat(timespec="seconds")
    if "source" not in df.columns:
        df["source"] = "AKSHARE_EASTMONEY_A_SPOT"
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
        return pd.DataFrame([{
            "trade_date": trade_date,
            "valid_count": valid_count,
            "quality": "WARN_BREADTH_SPARSE" if valid_count > 0 else "WARN_BREADTH_MISSING",
        }])
    denom = max(valid_count, 1)
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
        "volume_ratio_median": float(pd.to_numeric(work[volume_ratio_col], errors="coerce").median()) if volume_ratio_col else None,
        "quality": "OK",
    }
    return pd.DataFrame([out])
