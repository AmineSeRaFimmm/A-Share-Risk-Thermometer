"""Map Shenwan L1 / style sector names to preferred ETFs."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from src.utils.config import load_yaml

QUALITY_CN = {
    "good": "贴合",
    "proxy": "主题代理",
    "weak": "弱代理",
}


@lru_cache(maxsize=1)
def _raw_map() -> dict[str, Any]:
    data = load_yaml("sector_etf_map.yml")
    return data if isinstance(data, dict) else {}


def clear_etf_map_cache() -> None:
    _raw_map.cache_clear()


def map_csi300() -> dict[str, Any]:
    raw = _raw_map().get("csi300") or {}
    return _normalize_entry("沪深300", raw, default_code="510300", default_name="沪深300ETF")


def map_sector(sector_name: str) -> dict[str, Any]:
    name = str(sector_name or "").strip()
    if name in {"恒生科技", "HSTECH", "hstech"}:
        raw = _raw_map().get("hstech") or (_raw_map().get("sectors") or {}).get("恒生科技") or {}
        return _normalize_entry("恒生科技", raw, default_code="513180", default_name="恒生科技ETF")
    if name in {"沪深300", "CSI300", "csi300"}:
        return map_csi300()
    sectors = _raw_map().get("sectors") or {}
    raw = sectors.get(name) or {}
    return _normalize_entry(name, raw)


def _normalize_entry(
    sector_name: str,
    raw: dict[str, Any],
    default_code: str = "",
    default_name: str = "",
) -> dict[str, Any]:
    code = str(raw.get("code") or default_code or "").zfill(6) if (raw.get("code") or default_code) else ""
    name = str(raw.get("name") or default_name or "")
    quality = str(raw.get("quality") or ("good" if code else "missing"))
    label = f"{code} {name}".strip() if code else sector_name
    return {
        "sector": sector_name,
        "etf_code": code,
        "etf_name": name,
        "etf_label": label,
        "exchange": str(raw.get("exchange") or _guess_exchange(code)),
        "alt_codes": [str(x).zfill(6) for x in (raw.get("alt") or [])],
        "note": str(raw.get("note") or ""),
        "quality": quality,
        "quality_cn": QUALITY_CN.get(quality, quality),
        "display": f"{sector_name} → {label}" if code else sector_name,
    }


def _guess_exchange(code: str) -> str:
    if not code:
        return ""
    if code.startswith(("5", "6")):
        return "SH"
    if code.startswith(("1", "0", "3")):
        return "SZ"
    return ""


def attach_etf_fields(item: dict[str, Any], name_key: str = "name") -> dict[str, Any]:
    """Return a copy of order/sector card with ETF fields filled."""
    out = dict(item)
    sector = str(out.get(name_key) or out.get("sector") or out.get("instrument") or "")
    # strip suffixes like " / 510300"
    if "→" in sector:
        sector = sector.split("→")[0].strip()
    if "/" in sector and any(ch.isdigit() for ch in sector):
        # e.g. 沪深300 / 510300
        left = sector.split("/")[0].strip()
        if left:
            sector = left.replace("等等价ETF", "").strip()
    mapped = map_sector(sector)
    out["sector"] = mapped["sector"]
    out["etf_code"] = mapped["etf_code"]
    out["etf_name"] = mapped["etf_name"]
    out["etf_label"] = mapped["etf_label"]
    out["etf_quality"] = mapped["quality"]
    out["etf_quality_cn"] = mapped["quality_cn"]
    out["etf_note"] = mapped["note"]
    out["etf_display"] = mapped["display"]
    # Keep human instrument readable
    if mapped["etf_code"]:
        out["instrument_display"] = f"{mapped['sector']}（{mapped['etf_code']} {mapped['etf_name']}）"
    else:
        out["instrument_display"] = mapped["sector"]
    return out


def all_sector_mappings() -> list[dict[str, Any]]:
    rows = [map_csi300(), map_sector("恒生科技")]
    for name in sorted((_raw_map().get("sectors") or {}).keys()):
        rows.append(map_sector(name))
    # dedupe by sector
    seen = set()
    out = []
    for r in rows:
        if r["sector"] in seen:
            continue
        seen.add(r["sector"])
        out.append(r)
    return out
