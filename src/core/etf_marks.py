"""Daily ETF OHLC marks for Flex simulation book (EOD professional policy).

Policy (must match product):
  - Sim entry price  = open on entry_date (T+1 open after signal)
  - Sim mark price   = close on as_of trade date
  - No intraday polling; rebuilt with site daily data

Research / paper use only — not a broker feed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.sector_etf_map import all_sector_mappings, map_csi300, map_sector
from src.storage.paths import CALCULATED, SITE, ensure_dirs
from src.storage.json_store import write_json

CACHE_DIR = CALCULATED / "etf_daily_cache"
DEFAULT_LOOKBACK_CALENDAR_DAYS = 120
FINAL_QUOTE_QUALITY = "OK_FINAL_QUOTE_PENDING_DAILY_CONFIRMATION"


def _today_cn() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def collect_flex_etf_codes(
    playbook: dict[str, Any] | None = None,
    *,
    include_all_primary: bool = False,
    include_full_map: bool = False,
) -> list[str]:
    """ETF codes needed for Flex sim EOD marks.

    Default: CSI300 + codes on current panel/position_state (professional, small set).
    include_all_primary keeps every preferred sector ETF available for durable
    local holdings. include_full_map additionally includes alternate proxies.
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

    if include_all_primary or include_full_map:
        for row in all_sector_mappings():
            if row.get("etf_code"):
                codes.add(str(row["etf_code"]).zfill(6))
            if include_full_map:
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


def _parse_tencent_final_quotes(text: str, codes: list[str], target: str) -> dict[str, dict[str, Any]]:
    expected = set(codes)
    target_compact = target.replace("-", "")
    quotes: dict[str, dict[str, Any]] = {}
    for match in re.finditer(r'v_(?:sh|sz)(\d{6})="([^"]*)"', text or ""):
        code, raw = match.groups()
        if code not in expected:
            continue
        fields = raw.split("~")
        try:
            stamp = fields[30]
            if stamp[:8] != target_compact or stamp[8:12] < "1500":
                continue
            values = {
                "open": float(fields[5]),
                "close": float(fields[3]),
                "high": float(fields[33]),
                "low": float(fields[34]),
            }
            if not all(value > 0 for value in values.values()):
                continue
        except (IndexError, TypeError, ValueError):
            continue
        quotes[code] = {
            **values,
            "source": "TENCENT_ETF_FINAL_QUOTE",
            "quote_time": f"{target}T{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}+08:00",
        }
    return quotes


def _parse_sina_final_quotes(text: str, codes: list[str], target: str) -> dict[str, dict[str, Any]]:
    expected = set(codes)
    quotes: dict[str, dict[str, Any]] = {}
    for match in re.finditer(r'var hq_str_(?:sh|sz)(\d{6})="([^"]*)"', text or ""):
        code, raw = match.groups()
        if code not in expected:
            continue
        fields = raw.split(",")
        try:
            quote_date = fields[30]
            quote_clock = fields[31]
            if quote_date != target or quote_clock[:5] < "15:00":
                continue
            values = {
                "open": float(fields[1]),
                "close": float(fields[3]),
                "high": float(fields[4]),
                "low": float(fields[5]),
            }
            if not all(value > 0 for value in values.values()):
                continue
        except (IndexError, TypeError, ValueError):
            continue
        quotes[code] = {
            **values,
            "source": "SINA_ETF_FINAL_QUOTE",
            "quote_time": f"{quote_date}T{quote_clock}+08:00",
        }
    return quotes


def fetch_etf_final_quotes(codes: list[str], target: str) -> dict[str, dict[str, Any]]:
    """Fetch same-day post-close OHLC snapshots, Tencent then Sina."""
    import requests

    _disable_proxies()
    normalized = sorted(set(str(code).zfill(6) for code in codes))
    symbols = [
        ("sz" if code.startswith(("15", "16", "18")) else "sh") + code
        for code in normalized
    ]
    quotes: dict[str, dict[str, Any]] = {}
    try:
        response = requests.get(
            "https://qt.gtimg.cn/q=" + ",".join(symbols),
            timeout=15,
        )
        response.raise_for_status()
        quotes.update(_parse_tencent_final_quotes(response.content.decode("gb18030", "replace"), normalized, target))
    except Exception as exc:  # noqa: BLE001
        print(f"WARN ETF final quote Tencent failed: {exc}")

    missing = [code for code in normalized if code not in quotes]
    if missing:
        missing_symbols = [
            ("sz" if code.startswith(("15", "16", "18")) else "sh") + code
            for code in missing
        ]
        try:
            response = requests.get(
                "https://hq.sinajs.cn/list=" + ",".join(missing_symbols),
                headers={"Referer": "https://finance.sina.com.cn/"},
                timeout=15,
            )
            response.raise_for_status()
            quotes.update(_parse_sina_final_quotes(response.content.decode("gb18030", "replace"), missing, target))
        except Exception as exc:  # noqa: BLE001
            print(f"WARN ETF final quote Sina failed: {exc}")
    return quotes


def complete_payload_with_final_quotes(
    payload: dict[str, Any],
    *,
    codes: list[str],
    target: str,
    quotes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Complete a lagging daily payload with timestamped post-close OHLC."""
    out = json.loads(json.dumps(payload))
    by_code = out.setdefault("by_code", {})
    normalized = sorted(set(str(code).zfill(6) for code in codes))
    used: dict[str, dict[str, str]] = {}
    for code in normalized:
        quote = quotes.get(code)
        if not quote:
            continue
        item = by_code.setdefault(code, {"etf_code": code, "bars": {}})
        bars = item.setdefault("bars", {})
        bars[target] = {
            key: round(float(quote[key]), 4)
            for key in ("open", "close", "high", "low")
        }
        dates = sorted(bars)
        item.update({
            "bar_count": len(dates),
            "first": dates[0],
            "last": dates[-1],
            "fresh_for_as_of": dates[-1] >= target,
        })
        item.setdefault("bar_sources", {})[target] = str(quote.get("source") or "FINAL_QUOTE")
        used[code] = {
            "source": str(quote.get("source") or "FINAL_QUOTE"),
            "quote_time": str(quote.get("quote_time") or ""),
        }

    missing = [code for code in normalized if not by_code.get(code, {}).get("bars")]
    stale = [
        code for code in normalized
        if by_code.get(code, {}).get("bars") and max(by_code[code]["bars"]) < target
    ]
    common_dates: set[str] | None = None
    for code in normalized:
        dates = set((by_code.get(code, {}).get("bars") or {}).keys())
        if not dates:
            continue
        common_dates = dates if common_dates is None else common_dates & dates
    complete_as_of = max(common_dates) if common_dates and not missing else None
    complete = complete_as_of == target and not missing and not stale
    out.update({
        "code_count": len(by_code),
        "missing_codes": missing,
        "stale_codes": stale,
        "complete_as_of": complete_as_of,
        "quality": FINAL_QUOTE_QUALITY if complete else "WARN_INCOMPLETE_AS_OF",
        "eod_completion_source": "POST_CLOSE_FINAL_QUOTE" if used else None,
        "final_quote_fallback": {
            "trade_date": target,
            "status": "complete" if complete else "incomplete",
            "code_count": len(used),
            "quotes": used,
        },
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    return out


def merge_published_etf_marks(
    candidate: dict[str, Any],
    published: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep a newer published close when a routine rebuild source is lagging.

    Candidate bars always win on matching dates, so a later official daily bar
    replaces the provisional final quote. Published-only dates are carried
    forward only through the candidate as_of boundary.
    """
    if not published:
        return candidate
    target = str(candidate.get("as_of") or "")[:10]
    if not target:
        return candidate

    out = json.loads(json.dumps(candidate))
    candidate_by = candidate.get("by_code") or {}
    published_by = published.get("by_code") or {}
    expected = sorted(
        set(candidate.get("coverage_codes") or [])
        | set(candidate_by)
        | set(candidate.get("missing_codes") or [])
    )
    if not expected:
        return candidate
    required = sorted(set(candidate.get("required_codes") or expected))

    merged_by: dict[str, Any] = {}
    carried_dates: dict[str, list[str]] = {}
    has_provisional_target = False
    for code in expected:
        fresh_item = candidate_by.get(code) or {}
        old_item = published_by.get(code) or {}
        old_bars = {
            day: bar for day, bar in (old_item.get("bars") or {}).items()
            if day <= target
        }
        fresh_bars = {
            day: bar for day, bar in (fresh_item.get("bars") or {}).items()
            if day <= target
        }
        bars = {**old_bars, **fresh_bars}
        if not bars:
            continue

        old_sources = dict(old_item.get("bar_sources") or {})
        fresh_sources = dict(fresh_item.get("bar_sources") or {})
        sources = {day: source for day, source in old_sources.items() if day in bars}
        for day in fresh_bars:
            if day in fresh_sources:
                sources[day] = fresh_sources[day]
            else:
                sources.pop(day, None)

        carried = sorted(set(old_bars) - set(fresh_bars))
        if carried:
            carried_dates[code] = carried
        has_provisional_target = has_provisional_target or (
            "FINAL_QUOTE" in str(sources.get(target) or "")
        )
        dates = sorted(bars)
        item = {
            **old_item,
            **fresh_item,
            "etf_code": code,
            "bars": bars,
            "bar_count": len(dates),
            "first": dates[0],
            "last": dates[-1],
            "fresh_for_as_of": dates[-1] >= target,
        }
        if sources:
            item["bar_sources"] = sources
        else:
            item.pop("bar_sources", None)
        merged_by[code] = item

    missing = [code for code in required if not merged_by.get(code, {}).get("bars")]
    stale = [
        code for code in required
        if merged_by.get(code, {}).get("bars") and max(merged_by[code]["bars"]) < target
    ]
    common_dates: set[str] | None = None
    for code in required:
        dates = set((merged_by.get(code, {}).get("bars") or {}).keys())
        if not dates:
            continue
        common_dates = dates if common_dates is None else common_dates & dates
    complete_as_of = max(common_dates) if common_dates and not missing else None
    coverage_missing = [code for code in expected if not merged_by.get(code, {}).get("bars")]
    coverage_stale = [
        code for code in expected
        if merged_by.get(code, {}).get("bars") and max(merged_by[code]["bars"]) < target
    ]
    coverage_common_dates: set[str] | None = None
    for code in expected:
        dates = set((merged_by.get(code, {}).get("bars") or {}).keys())
        if not dates:
            continue
        coverage_common_dates = dates if coverage_common_dates is None else coverage_common_dates & dates
    coverage_complete_as_of = (
        max(coverage_common_dates)
        if coverage_common_dates and not coverage_missing
        else None
    )
    complete = complete_as_of == target and not missing and not stale
    quality = (
        FINAL_QUOTE_QUALITY if complete and has_provisional_target
        else "OK" if complete
        else "WARN_INCOMPLETE_AS_OF"
    )
    out.update({
        "by_code": merged_by,
        "code_count": len(merged_by),
        "missing_codes": missing,
        "stale_codes": stale,
        "complete_as_of": complete_as_of,
        "coverage_missing_codes": coverage_missing,
        "coverage_stale_codes": coverage_stale,
        "coverage_complete_as_of": coverage_complete_as_of,
        "quality": quality,
        "published_bar_preservation": {
            "status": "used" if carried_dates else "not_needed",
            "candidate_as_of": target,
            "codes": sorted(carried_dates),
            "dates_by_code": carried_dates,
        },
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    if has_provisional_target:
        if carried_dates:
            out["eod_completion_source"] = "PRESERVED_POST_CLOSE_FINAL_QUOTE"
        else:
            out.setdefault("eod_completion_source", "POST_CLOSE_FINAL_QUOTE")
        if published.get("final_quote_fallback"):
            out["final_quote_fallback"] = published["final_quote_fallback"]
    return out


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
    max_workers: int = 4,
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

    required_codes = collect_flex_etf_codes(playbook)
    codes = collect_flex_etf_codes(playbook, include_all_primary=True)
    by_code: dict[str, Any] = {}
    missing: list[str] = []
    stale: list[str] = []
    common_dates: set[str] | None = None
    coverage_missing: list[str] = []
    coverage_stale: list[str] = []
    coverage_common_dates: set[str] | None = None
    bars_by_code: dict[str, dict[str, dict[str, float]]] = {}

    def load_one(code: str) -> tuple[str, dict[str, dict[str, float]]]:
        frame = load_or_fetch_etf_bars(code, start=start, end=end, force_fetch=force_fetch)
        return code, bars_to_dict(frame)

    workers = max(1, min(int(max_workers), len(codes) or 1))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="etf-mark") as pool:
        futures = [pool.submit(load_one, code) for code in codes]
        for future in as_completed(futures):
            code, bars = future.result()
            bars_by_code[code] = bars

    for code in codes:
        bars = bars_by_code.get(code, {})
        if not bars:
            coverage_missing.append(code)
            if code in required_codes:
                missing.append(code)
            continue
        by_code[code] = {
            "etf_code": code,
            "bars": bars,
            "bar_count": len(bars),
            "first": min(bars),
            "last": max(bars),
            "fresh_for_as_of": max(bars) >= end,
        }
        dates = {day for day in bars if day <= end}
        coverage_common_dates = dates if coverage_common_dates is None else coverage_common_dates & dates
        if code in required_codes:
            common_dates = dates if common_dates is None else common_dates & dates
        if max(bars) < end:
            coverage_stale.append(code)
            if code in required_codes:
                stale.append(code)

    complete_as_of = max(common_dates) if common_dates and not missing else None
    coverage_complete_as_of = (
        max(coverage_common_dates)
        if coverage_common_dates and not coverage_missing
        else None
    )

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
        "fetch_workers": workers,
        "not_broker_feed": True,
        "required_codes": required_codes,
        "coverage_codes": codes,
        "code_count": len(by_code),
        "missing_codes": missing,
        "stale_codes": stale,
        "complete_as_of": complete_as_of,
        "coverage_missing_codes": coverage_missing,
        "coverage_stale_codes": coverage_stale,
        "coverage_complete_as_of": coverage_complete_as_of,
        "quality": "OK" if not missing and not stale and complete_as_of == end else "WARN_INCOMPLETE_AS_OF",
        "by_code": by_code,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def write_etf_marks_site(payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    payload = payload or build_etf_marks_payload(**kwargs)
    published = None
    path = SITE / "etf_daily_marks.json"
    if path.exists():
        try:
            published = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            published = None
    payload = merge_published_etf_marks(payload, published)
    write_json(payload, SITE / "etf_daily_marks.json")
    return payload
