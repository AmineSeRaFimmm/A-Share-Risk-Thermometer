"""CFFEX official daily statistics (日统计) for index options → local cache.

CFFEX publishes exchange EOD bars as XML (no auth API needed), e.g.:

    http://www.cffex.com.cn/sj/hqsj/rtj/202607/16/index.xml

We parse HS300 index options (product IO), map to the same per-contract CSV
schema as the Sina cache under ``data/raw/options_daily/io*.csv``, and merge
so official closes do not depend solely on Sina contract-by-contract scrapes.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
import re

import pandas as pd
import requests

from src.storage.csv_store import read_csv, write_csv
from src.storage.paths import RAW, ensure_dirs
from src.utils.retry import retry_call

CFFEX_RTJ_XML = "http://www.cffex.com.cn/sj/hqsj/rtj/{yyyymm}/{dd}/index.xml"
IO_INSTRUMENT_RE = re.compile(
    r"^IO(?P<yy>\d{2})(?P<mm>\d{2})-(?P<cp>[CP])-(?P<strike>\d+)$",
    re.IGNORECASE,
)
SOURCE = "CFFEX_RTJ_XML"
CACHE_COLS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "contract",
    "month",
    "cp",
    "strike",
    "source",
    "fetch_time",
]


def _norm_trade_date(trade_date: str) -> str:
    return str(pd.to_datetime(trade_date).date())


def cffex_rtj_xml_url(trade_date: str) -> str:
    d = pd.to_datetime(trade_date)
    return CFFEX_RTJ_XML.format(yyyymm=d.strftime("%Y%m"), dd=d.strftime("%d"))


def _is_xml_payload(content: bytes) -> bool:
    head = content.lstrip()[:80].lower()
    return head.startswith(b"<?xml") or head.startswith(b"<dailydatas")


def fetch_cffex_rtj_xml(trade_date: str, *, timeout: int = 30) -> bytes | None:
    """Download CFFEX daily-stats XML for ``trade_date``. None if missing/holiday."""
    url = cffex_rtj_xml_url(trade_date)

    def _get() -> bytes:
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; a-share-risk-thermometer/1.0)",
                "Accept": "application/xml,text/xml,*/*",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.content

    try:
        content = retry_call(_get, times=3, sleep_seconds=1.5)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN CFFEX RTJ fetch failed {trade_date}: {exc}")
        return None
    if not content or not _is_xml_payload(content):
        return None
    return content


def parse_cffex_io_daily_xml(content: bytes, trade_date: str | None = None) -> pd.DataFrame:
    """Parse CFFEX RTJ XML into option cache rows (IO only)."""
    root = ET.fromstring(content)
    fetched = datetime.now().isoformat(timespec="seconds")
    rows: list[dict] = []
    for node in root.findall(".//dailydata"):
        fields = {child.tag: (child.text or "").strip() for child in node}
        instrument = fields.get("instrumentid", "")
        m = IO_INSTRUMENT_RE.match(instrument)
        if not m:
            continue
        yy, mm, cp, strike_s = m.group("yy"), m.group("mm"), m.group("cp").upper(), m.group("strike")
        contract = f"io{yy}{mm}{cp.lower()}{strike_s}"
        day_raw = fields.get("tradingday") or ""
        if len(day_raw) == 8 and day_raw.isdigit():
            day = f"{day_raw[:4]}-{day_raw[4:6]}-{day_raw[6:8]}"
        elif trade_date:
            day = _norm_trade_date(trade_date)
        else:
            continue

        def _num(key: str) -> float | None:
            raw = fields.get(key, "")
            if raw is None or raw == "":
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        settle = _num("settlementpriceif")
        if settle is None:
            settle = _num("settlementprice")
        close = _num("closeprice")
        if close is None or close <= 0:
            close = settle
        if close is None or close <= 0:
            continue
        open_px = _num("openprice")
        high_px = _num("highestprice")
        low_px = _num("lowestprice")
        # Zero-volume contracts often have blank OHLC; use close for OHLC fill.
        if open_px is None or open_px <= 0:
            open_px = close
        if high_px is None or high_px <= 0:
            high_px = max(open_px, close)
        if low_px is None or low_px <= 0:
            low_px = min(open_px, close)
        vol = _num("volume")
        rows.append(
            {
                "date": day,
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close,
                "volume": 0.0 if vol is None else vol,
                "contract": contract,
                "month": f"20{yy}-{mm}",
                "cp": cp,
                "strike": int(strike_s),
                "source": SOURCE,
                "fetch_time": fetched,
            }
        )
    if not rows:
        return pd.DataFrame(columns=CACHE_COLS)
    out = pd.DataFrame(rows)
    return out[CACHE_COLS].sort_values(["contract", "date"]).reset_index(drop=True)


def _merge_contract_day(path: Path, day_row: pd.DataFrame) -> bool:
    """Merge one contract-day into cache CSV. Returns True if file changed."""
    assert len(day_row) == 1
    day = str(day_row.iloc[0]["date"])
    if path.exists():
        old = read_csv(path)
        if old.empty:
            merged = day_row.copy()
        else:
            # CFFEX EOD wins for that date over Sina/other scrapes.
            keep = old[old["date"].astype(str) != day].copy()
            merged = pd.concat([keep, day_row], ignore_index=True)
    else:
        merged = day_row.copy()
    for col in CACHE_COLS:
        if col not in merged.columns:
            merged[col] = pd.NA
    merged = merged[CACHE_COLS].sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    prev = read_csv(path) if path.exists() else pd.DataFrame()
    if not prev.empty and len(prev) == len(merged):
        # cheap equality on key fields for the target day
        try:
            a = prev[prev["date"].astype(str) == day]
            b = merged[merged["date"].astype(str) == day]
            if not a.empty and not b.empty:
                same = (
                    float(a.iloc[0]["close"]) == float(b.iloc[0]["close"])
                    and str(a.iloc[0].get("source", "")) == SOURCE
                )
                if same and len(prev) == len(merged):
                    return False
        except Exception:
            pass
    write_csv(merged, path)
    return True


def write_cffex_io_to_option_cache(day_df: pd.DataFrame) -> dict[str, int]:
    """Write parsed IO day rows into ``data/raw/options_daily``."""
    ensure_dirs()
    if day_df is None or day_df.empty:
        return {"contracts": 0, "files_written": 0, "rows": 0}
    out_dir = RAW / "options_daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for contract, grp in day_df.groupby("contract"):
        # one row per date (XML is daily)
        for _, row in grp.iterrows():
            path = out_dir / f"{str(contract).lower()}.csv"
            if _merge_contract_day(path, pd.DataFrame([row])[CACHE_COLS]):
                written += 1
    return {
        "contracts": int(day_df["contract"].nunique()),
        "files_written": written,
        "rows": int(len(day_df)),
    }


def save_raw_cffex_rtj_xml(trade_date: str, content: bytes) -> Path:
    ensure_dirs()
    raw_dir = RAW / "cffex_rtj"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{_norm_trade_date(trade_date)}.xml"
    path.write_bytes(content)
    return path


def sync_cffex_io_daily_stats(
    trade_dates: str | list[str],
    *,
    save_raw: bool = True,
) -> dict[str, object]:
    """Fetch CFFEX日统计 for date(s) and merge IO bars into option cache.

    Parameters
    ----------
    trade_dates:
        One date or a list (``YYYY-MM-DD``). Non-trading days are skipped quietly.
    """
    if isinstance(trade_dates, str):
        dates = [_norm_trade_date(trade_dates)]
    else:
        dates = sorted({_norm_trade_date(d) for d in trade_dates if d})

    summary: dict[str, object] = {
        "source": SOURCE,
        "requested_dates": dates,
        "ok_dates": [],
        "missing_dates": [],
        "contracts": 0,
        "files_written": 0,
        "rows": 0,
    }
    total_contracts = 0
    total_written = 0
    total_rows = 0
    for day in dates:
        content = fetch_cffex_rtj_xml(day)
        if content is None:
            summary["missing_dates"].append(day)  # type: ignore[attr-defined]
            continue
        if save_raw:
            save_raw_cffex_rtj_xml(day, content)
        day_df = parse_cffex_io_daily_xml(content, trade_date=day)
        if day_df.empty:
            summary["missing_dates"].append(day)  # type: ignore[attr-defined]
            continue
        stats = write_cffex_io_to_option_cache(day_df)
        summary["ok_dates"].append(day)  # type: ignore[attr-defined]
        total_contracts += int(stats["contracts"])
        total_written += int(stats["files_written"])
        total_rows += int(stats["rows"])
        print(
            f"CFFEX RTJ IO {day}: contracts={stats['contracts']} "
            f"rows={stats['rows']} files_touched={stats['files_written']}"
        )
    summary["contracts"] = total_contracts
    summary["files_written"] = total_written
    summary["rows"] = total_rows
    return summary


def sync_cffex_io_for_index_gap(
    index_history: pd.DataFrame,
    *,
    lookback_trading_days: int = 15,
    avix_clean_max: str | None = None,
) -> dict[str, object]:
    """Sync CFFEX IO for recent HS300 sessions, focusing on dates after avix max."""
    if index_history is None or index_history.empty:
        return {"source": SOURCE, "ok_dates": [], "missing_dates": [], "rows": 0}
    hs = index_history
    if "symbol" in hs.columns:
        hs = hs[hs["symbol"] == "sh000300"].copy()
    if hs.empty or "date" not in hs.columns:
        return {"source": SOURCE, "ok_dates": [], "missing_dates": [], "rows": 0}
    days = (
        pd.to_datetime(hs["date"], errors="coerce")
        .dropna()
        .dt.strftime("%Y-%m-%d")
        .drop_duplicates()
        .tolist()
    )
    if not days:
        return {"source": SOURCE, "ok_dates": [], "missing_dates": [], "rows": 0}
    recent = days[-max(1, lookback_trading_days) :]
    if avix_clean_max:
        # Always include gap after official AVIX tip, plus the latest session.
        gap = [d for d in recent if d > str(avix_clean_max)[:10]]
        if days[-1] not in gap:
            gap.append(days[-1])
        # Also re-sync a few days before tip so recompute tail has exchange bars.
        pre = [d for d in recent if d <= str(avix_clean_max)[:10]][-5:]
        target = sorted(set(pre + gap))
    else:
        target = recent
    return sync_cffex_io_daily_stats(target)
