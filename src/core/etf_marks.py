"""Daily ETF OHLC marks for Flex simulation book (EOD professional policy).

Policy (must match product):
  - Sim entry price  = open on entry_date (T+1 open after signal)
  - Sim mark price   = close on as_of trade date
  - No intraday polling; rebuilt with site daily data

Research / paper use only — not a broker feed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.sector_etf_map import all_sector_mappings, map_csi300, map_sector
from src.storage.paths import CALCULATED, SITE, ensure_dirs
from src.storage.json_store import write_json

CACHE_DIR = CALCULATED / "etf_daily_cache"
DEFAULT_LOOKBACK_CALENDAR_DAYS = 120


def _today_cn() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def collect_flex_etf_codes(
    playbook: dict[str, Any] | None = None,
    *,
    include_full_map: bool = False,
) -> list[str]:
    """ETF codes needed for Flex sim EOD marks.

    Default: CSI300 + codes on current panel/position_state (professional, small set).
    Optional full sector map only when include_full_map=True (slow / more failure surface).
    """
    codes: set[str] = set()
    csi = map_csi300()
    if csi.get("etf_code"):
        codes.add(str(csi["etf_code"]).zfill(6))

    if playbook:
        flex = playbook.get("flex_panel") or playbook
        for lst_key in ("buy_list", "hold_list", "close_list", "sell_list", "avoid_list", "minimal_actions"):
            for item in flex.get(lst_key) or []:
                code = item.get("etf_code") or item.get("code")
                if code:
                    codes.add(str(code).zfill(6))
        pos = flex.get("position_state") or {}
        for sleeve in ("core", "satellite"):
            d = pos.get(sleeve) or {}
            if d.get("etf_code"):
                codes.add(str(d["etf_code"]).zfill(6))
            for name in d.get("names") or []:
                m = map_csi300() if str(name) in {"沪深300", "CSI300"} else map_sector(str(name))
                if m.get("etf_code"):
                    codes.add(str(m["etf_code"]).zfill(6))
        for item in (flex.get("satellite") or {}).get("buy") or []:
            if item.get("etf_code"):
                codes.add(str(item["etf_code"]).zfill(6))
        # core sleeve default
        core = flex.get("core") or {}
        if core.get("etf_code"):
            codes.add(str(core["etf_code"]).zfill(6))

    if include_full_map:
        for row in all_sector_mappings():
            if row.get("etf_code"):
                codes.add(str(row["etf_code"]).zfill(6))
            for alt in row.get("alt_codes") or []:
                if alt:
                    codes.add(str(alt).zfill(6))

    return sorted(c for c in codes if c and c.isdigit() and len(c) == 6)


def _cache_path(code: str) -> Path:
    return CACHE_DIR / f"{code}.csv"


def _normalize_ohlc_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    rename = {
        "日期": "trade_date",
        "date": "trade_date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
    }
    out = df.rename(columns={k: v for k, v in rename.items() if k in df.columns}).copy()
    if "trade_date" not in out.columns:
        return pd.DataFrame()
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.strftime("%Y-%m-%d")
    for c in ("open", "close", "high", "low"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.dropna(subset=["trade_date", "open", "close"]).sort_values("trade_date")


def _disable_proxies() -> None:
    import os

    for k in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        os.environ.pop(k, None)
    # Force requests/urllib to ignore residual env proxies.
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        import requests

        _orig = requests.Session.request

        def _no_proxy_request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.setdefault("proxies", {"http": None, "https": None})
            return _orig(self, method, url, **kwargs)

        if getattr(requests.Session.request, "_flex_no_proxy", False) is not True:
            _no_proxy_request._flex_no_proxy = True  # type: ignore[attr-defined]
            requests.Session.request = _no_proxy_request  # type: ignore[method-assign]
    except Exception:
        pass


def _fetch_etf_hist_em(code: str, start: str, end: str) -> pd.DataFrame:
    import time

    _disable_proxies()
    import akshare as ak

    start_s = start.replace("-", "")
    end_s = end.replace("-", "")
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            df = ak.fund_etf_hist_em(
                symbol=code,
                period="daily",
                start_date=start_s,
                end_date=end_s,
                adjust="",
            )
            out = _normalize_ohlc_frame(df)
            if not out.empty:
                return out
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(0.8 * (attempt + 1))
    # Fallback: Sina fund hist (symbol prefix sh/sz)
    try:
        prefix = "sh" if code.startswith(("5", "6")) else "sz"
        df = ak.fund_etf_hist_sina(symbol=f"{prefix}{code}")
        out = _normalize_ohlc_frame(df)
        if not out.empty:
            mask = (out["trade_date"] >= start) & (out["trade_date"] <= end)
            return out.loc[mask].copy()
    except Exception as exc:  # noqa: BLE001
        last_exc = exc
    if last_exc:
        raise last_exc
    return pd.DataFrame()


def load_or_fetch_etf_bars(
    code: str,
    *,
    start: str,
    end: str,
    force_fetch: bool = False,
) -> pd.DataFrame:
    """Return daily bars for code; disk cache + optional network refresh."""
    ensure_dirs()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(code)
    cached = pd.DataFrame()
    if path.exists() and not force_fetch:
        try:
            cached = pd.read_csv(path)
            if not cached.empty and "trade_date" in cached.columns:
                cached["trade_date"] = pd.to_datetime(cached["trade_date"]).dt.strftime("%Y-%m-%d")
        except Exception:
            cached = pd.DataFrame()

    need_fetch = force_fetch or cached.empty
    if not need_fetch and not cached.empty:
        cmax = str(cached["trade_date"].max())
        if cmax < end:
            need_fetch = True

    if need_fetch:
        try:
            fresh = _fetch_etf_hist_em(code, start, end)
            if not fresh.empty:
                if not cached.empty:
                    cached = (
                        pd.concat([cached, fresh], ignore_index=True)
                        .drop_duplicates("trade_date", keep="last")
                        .sort_values("trade_date")
                    )
                else:
                    cached = fresh
                cached.to_csv(path, index=False)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN etf_marks fetch {code}: {exc}")

    if cached.empty:
        return cached
    mask = (cached["trade_date"] >= start) & (cached["trade_date"] <= end)
    return cached.loc[mask].copy()


def bars_to_dict(df: pd.DataFrame) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    if df is None or df.empty:
        return out
    for _, row in df.iterrows():
        d = str(row["trade_date"])[:10]
        try:
            out[d] = {
                "open": round(float(row["open"]), 4),
                "close": round(float(row["close"]), 4),
                "high": round(float(row["high"]), 4) if pd.notna(row.get("high")) else round(float(row["close"]), 4),
                "low": round(float(row["low"]), 4) if pd.notna(row.get("low")) else round(float(row["close"]), 4),
            }
        except Exception:
            continue
    return out


def build_etf_marks_payload(
    *,
    as_of: str | None = None,
    playbook: dict[str, Any] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_CALENDAR_DAYS,
    force_fetch: bool = False,
) -> dict[str, Any]:
    """Build site JSON for Flex sim EOD marking."""
    ensure_dirs()
    end = (as_of or _today_cn())[:10]
    try:
        end_dt = datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        end_dt = datetime.now()
        end = end_dt.strftime("%Y-%m-%d")
    start = (end_dt - timedelta(days=int(lookback_days))).strftime("%Y-%m-%d")

    if playbook is None:
        path = SITE / "stage_playbook.json"
        if path.exists():
            try:
                playbook = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                playbook = None

    codes = collect_flex_etf_codes(playbook)
    by_code: dict[str, Any] = {}
    missing: list[str] = []
    for code in codes:
        df = load_or_fetch_etf_bars(code, start=start, end=end, force_fetch=force_fetch)
        bars = bars_to_dict(df)
        if not bars:
            missing.append(code)
            continue
        by_code[code] = {
            "etf_code": code,
            "bars": bars,
            "bar_count": len(bars),
            "first": min(bars),
            "last": max(bars),
        }

    return {
        "title": "Flex ETF daily marks (EOD)",
        "policy": "SIM_ENTRY_OPEN_MARK_CLOSE",
        "policy_cn": (
            "模拟仓专业口径：入场价=入场日开盘价；盯市价=as_of收盘价；"
            "不做盘中轮询；与策略/回测日频一致。"
        ),
        "as_of": end,
        "start": start,
        "source": "AKSHARE_FUND_ETF_HIST_EM",
        "not_broker_feed": True,
        "code_count": len(by_code),
        "missing_codes": missing,
        "by_code": by_code,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def write_etf_marks_site(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    payload = payload or build_etf_marks_payload(**kwargs)
    write_json(payload, SITE / "etf_daily_marks.json")
    return payload
