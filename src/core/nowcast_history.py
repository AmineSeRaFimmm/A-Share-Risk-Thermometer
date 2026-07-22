from __future__ import annotations

import pandas as pd

from src.core.calendar import merged_trading_days
from src.data_sources.akshare_breadth import fetch_breadth_summary_multi
from src.data_sources.akshare_qvix import fetch_realtime_qvix_for_date
from src.core.realtime_avix import (
    calculate_realtime_avix,
    realtime_avix_allows_gap_fill,
)
from src.core.risk_temperature import compute_risk_temperature
from src.core.qvix_validation import validate_qvix
from src.core.realized_vol import compute_realized_vol
from src.core.drawdown import compute_drawdown
from src.core.breadth import compute_breadth_pressure, drop_legacy_synthetic_breadth
from src.storage.csv_store import read_csv
from src.storage.paths import CALCULATED, NORMALIZED, RAW
from src.utils.quality import merge_quality


def _finite(value):
    try:
        numeric = float(value)
    except Exception:
        return None
    return round(numeric, 4) if pd.notna(numeric) else None


def _latest_clean_before(clean: pd.DataFrame, trade_date: str) -> float | None:
    if clean.empty:
        return None
    frame = clean[clean["trade_date"].astype(str) < str(trade_date)].sort_values("trade_date")
    if frame.empty:
        return None
    value = pd.to_numeric(frame.iloc[-1].get("avix_clean"), errors="coerce")
    return None if pd.isna(value) else float(value)


def _realtime_avix_rows(
    official_clean: pd.DataFrame,
    rate_curve: pd.DataFrame,
    index_history: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, str]]:
    hs = index_history[index_history["symbol"].astype(str) == "sh000300"].copy()
    trading_days = set(merged_trading_days(hs))
    rows: list[dict] = []
    status_by_date: dict[str, str] = {}

    for path in sorted((RAW / "option_realtime").glob("*.csv")):
        if path.name == "fetch_manifest.csv":
            continue
        trade_date = path.stem
        raw = read_csv(path)
        if raw.empty:
            status_by_date[trade_date] = "实时期权链为空"
            continue
        _chain, result = calculate_realtime_avix(
            raw,
            rate_curve,
            trade_date,
            trading_days,
            close_avix=_latest_clean_before(official_clean, trade_date),
        )
        if result.empty:
            status_by_date[trade_date] = "实时AVIX计算无结果"
            continue
        row = result.iloc[-1].to_dict()
        quality = str(row.get("quality", ""))
        avix_mid = pd.to_numeric(row.get("avix_mid"), errors="coerce")
        # Gap-fill estimated close: strict OK only — never soft-WARN into history proxy.
        if realtime_avix_allows_gap_fill(quality, avix_mid):
            rows.append(row)
            status_by_date[trade_date] = "实时AVIX可用(严格OK，可补估算收盘)"
        elif pd.notna(avix_mid) and float(avix_mid) > 0:
            status_by_date[trade_date] = f"实时AVIX仅可盘中: {quality or 'UNKNOWN'}"
        else:
            status_by_date[trade_date] = f"实时AVIX不可用: {quality or 'UNKNOWN'}"

    return pd.DataFrame(rows), status_by_date


def _pseudo_avix_clean(official_clean: pd.DataFrame, realtime_avix: pd.DataFrame) -> pd.DataFrame:
    clean = official_clean.copy()
    if clean.empty or realtime_avix.empty:
        return clean
    estimate_rows = pd.DataFrame({
        "trade_date": realtime_avix["trade_date"].astype(str),
        "avix_clean": pd.to_numeric(realtime_avix["avix_mid"], errors="coerce"),
        "quality": "OK_REALTIME_AVIX_ESTIMATE",
        "near_expiry": realtime_avix.get("near_expiry"),
        "next_expiry": realtime_avix.get("next_expiry"),
        "near_dte": realtime_avix.get("near_dte"),
        "next_dte": realtime_avix.get("next_dte"),
        "near_var": realtime_avix.get("near_var"),
        "next_var": realtime_avix.get("next_var"),
        "near_n_options": realtime_avix.get("near_n_options"),
        "next_n_options": realtime_avix.get("next_n_options"),
    })
    estimate_rows = estimate_rows.dropna(subset=["trade_date", "avix_clean"])
    combined = pd.concat([clean, estimate_rows], ignore_index=True)
    return combined.drop_duplicates("trade_date", keep="last").sort_values("trade_date")


def _augment_breadth_for_realtime_dates(
    breadth_history: pd.DataFrame,
    realtime_avix: pd.DataFrame,
    fetcher=fetch_breadth_summary_multi,
) -> pd.DataFrame:
    """Add exact-date live breadth for estimated-close rows when official breadth lags."""
    base = breadth_history.copy() if breadth_history is not None and not breadth_history.empty else pd.DataFrame()
    if realtime_avix is None or realtime_avix.empty or fetcher is None:
        return base

    existing = set()
    if not base.empty and "trade_date" in base.columns:
        existing = set(base["trade_date"].astype(str))

    additions = []
    for trade_date in sorted(realtime_avix["trade_date"].dropna().astype(str).unique()):
        if trade_date in existing:
            continue
        try:
            summary = fetcher(trade_date)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN live breadth fetch failed {trade_date}: {exc}")
            continue
        if summary is None or summary.empty:
            continue
        row = summary.iloc[[0]].copy()
        if str(row.iloc[0].get("trade_date", ""))[:10] != trade_date:
            print(f"WARN live breadth date mismatch want={trade_date} got={row.iloc[0].get('trade_date')}")
            continue
        if not str(row.iloc[0].get("quality", "")).startswith("OK"):
            print(f"WARN live breadth weak {trade_date}: {row.iloc[0].get('quality')}")
            continue
        additions.append(row)

    if not additions:
        return base
    return (
        pd.concat([base, *additions], ignore_index=True)
        .drop_duplicates("trade_date", keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def _augment_qvix_for_realtime_dates(
    qvix_raw: pd.DataFrame,
    realtime_avix: pd.DataFrame,
    fetcher=fetch_realtime_qvix_for_date,
) -> pd.DataFrame:
    """Add exact-date realtime QVIX rows for estimated-close rows only."""
    base = qvix_raw.copy() if qvix_raw is not None and not qvix_raw.empty else pd.DataFrame()
    if realtime_avix is None or realtime_avix.empty or fetcher is None:
        return base

    existing = set()
    if not base.empty and "date" in base.columns:
        valid = base.copy()
        valid["close"] = pd.to_numeric(valid.get("close"), errors="coerce")
        existing = set(valid.loc[valid["close"].notna() & valid["close"].gt(0), "date"].astype(str))

    additions = []
    for trade_date in sorted(realtime_avix["trade_date"].dropna().astype(str).unique()):
        if trade_date in existing:
            continue
        try:
            row = fetcher(trade_date)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN realtime QVIX fetch failed {trade_date}: {exc}")
            continue
        if row is None or row.empty:
            continue
        row = row.iloc[[0]].copy()
        if str(row.iloc[0].get("date", ""))[:10] != trade_date:
            print(f"WARN realtime QVIX date mismatch want={trade_date} got={row.iloc[0].get('date')}")
            continue
        close = pd.to_numeric(row.iloc[0].get("close"), errors="coerce")
        if pd.isna(close) or float(close) <= 0:
            print(f"WARN realtime QVIX weak {trade_date}: close={row.iloc[0].get('close')}")
            continue
        additions.append(row)

    if not additions:
        return base
    return (
        pd.concat([base, *additions], ignore_index=True)
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _gap_rows(
    official_risk: pd.DataFrame,
    index_history: pd.DataFrame,
    realtime_status: dict[str, str],
    nowcast: pd.DataFrame,
) -> list[dict]:
    hs = index_history[index_history["symbol"].astype(str) == "sh000300"].copy()
    hs_dates = sorted(pd.to_datetime(hs["date"], errors="coerce").dropna().dt.strftime("%Y-%m-%d").unique().tolist())
    official_dates = set(official_risk["trade_date"].astype(str)) if not official_risk.empty else set()
    official_latest = max(official_dates) if official_dates else None
    nowcast_dates = set(nowcast["trade_date"].astype(str)) if not nowcast.empty else set()
    out = []
    for date in hs_dates:
        if official_latest and date <= official_latest:
            continue
        if date in official_dates:
            continue
        status = realtime_status.get(date)
        if date in nowcast_dates:
            reason = "正式期权日线缺失或不合格；使用实时AVIX生成估算温度"
            estimate_status = "可用"
        elif status:
            reason = status
            estimate_status = "不可用"
        else:
            reason = "无正式AVIX且没有可用实时期权链"
            estimate_status = "不可用"
        out.append({
            "date": date,
            "official_status": "缺失",
            "estimate_status": estimate_status,
            "reason": reason,
        })
    return out


def build_nowcast_history(
    official_risk: pd.DataFrame,
    official_clean: pd.DataFrame,
    qvix_raw: pd.DataFrame,
    rate_curve: pd.DataFrame,
    index_history: pd.DataFrame,
    breadth_history: pd.DataFrame,
) -> dict:
    if official_risk.empty or official_clean.empty or index_history.empty or rate_curve.empty:
        return {
            "status": "missing_inputs",
            "rows": [],
            "gaps": [],
            "methodology": {},
        }

    realtime_avix, realtime_status = _realtime_avix_rows(official_clean, rate_curve, index_history)
    official_latest = str(official_risk["trade_date"].max())
    realtime_avix = realtime_avix[realtime_avix["trade_date"].astype(str) > official_latest].copy()
    if realtime_avix.empty:
        return {
            "status": "no_estimates",
            "official_latest_date": official_latest,
            "rows": [],
            "gaps": _gap_rows(official_risk, index_history, realtime_status, pd.DataFrame()),
            "methodology": _methodology(),
        }

    pseudo_clean = _pseudo_avix_clean(official_clean, realtime_avix)
    qvix_source = _augment_qvix_for_realtime_dates(qvix_raw, realtime_avix)
    qvix = validate_qvix(pseudo_clean, qvix_source)
    realized = compute_realized_vol(index_history)
    drawdown = compute_drawdown(index_history)
    breadth_source = _augment_breadth_for_realtime_dates(
        drop_legacy_synthetic_breadth(breadth_history),
        realtime_avix,
    )
    breadth = compute_breadth_pressure(breadth_source)
    estimated = compute_risk_temperature(pseudo_clean, qvix, realized, drawdown, breadth, index_history)
    estimated = estimated[estimated["trade_date"].astype(str).isin(set(realtime_avix["trade_date"].astype(str)))].copy()
    if estimated.empty:
        return {
            "status": "no_estimates",
            "official_latest_date": official_latest,
            "rows": [],
            "gaps": _gap_rows(official_risk, index_history, realtime_status, pd.DataFrame()),
            "methodology": _methodology(),
        }

    realtime_cols = [
        "trade_date", "valuation_time", "avix_mid", "near_expiry", "next_expiry",
        "near_dte", "next_dte", "near_n_options", "next_n_options", "quality", "note",
    ]
    estimated = estimated.merge(
        realtime_avix[[col for col in realtime_cols if col in realtime_avix.columns]].rename(columns={"quality": "realtime_avix_quality"}),
        on="trade_date",
        how="left",
    )
    estimated["temperature_mode"] = "ESTIMATED_CLOSE"
    estimated["temperature_mode_cn"] = "估算收盘"
    estimated["baseline_trade_date"] = official_latest
    estimated["quality"] = estimated["quality"].map(
        lambda q: merge_quality(["OK_ESTIMATED_CLOSE", q, "WARN_OFFICIAL_AVIX_MISSING"])
    )
    estimated["gap_reason"] = "正式期权日线缺失或不合格；使用实时AVIX估算"

    rows = []
    for row in estimated.sort_values("trade_date").itertuples(index=False):
        rows.append({
            "date": row.trade_date,
            "trade_date": row.trade_date,
            "risk_temperature_estimated": _finite(row.risk_temperature),
            "regime": row.regime,
            "regime_cn": row.regime_cn,
            "temperature_mode": row.temperature_mode,
            "temperature_mode_cn": row.temperature_mode_cn,
            "quality": row.quality,
            "baseline_trade_date": row.baseline_trade_date,
            "gap_reason": row.gap_reason,
            "avix_realtime_mid": _finite(getattr(row, "avix_mid", None)),
            "avix_realtime_quality": getattr(row, "realtime_avix_quality", None),
            "realtime_valuation_time": getattr(row, "valuation_time", None),
            "hs300_close": _finite(getattr(row, "sh000300_close", None)),
            "qvix_close": _finite(getattr(row, "qvix_close", None)),
            "qvix_source": getattr(row, "qvix_source", None),
            "drawdown_pressure": _finite(getattr(row, "drawdown_pressure", None)),
            "breadth_pressure": _finite(getattr(row, "market_breadth_pressure", None)),
            "model_confidence": _finite(getattr(row, "model_confidence", None)),
            "model_missing_components": getattr(row, "model_missing_components", None),
            "components": {
                "avix_percentile_2y": _finite(getattr(row, "avix_percentile_2y", None)),
                "avix_zscore_1y": _finite(getattr(row, "avix_zscore_1y", None)),
                "avix_5d_change": _finite(getattr(row, "avix_5d_change", None)),
                "qvix_confirmation": _finite(getattr(row, "qvix_confirmation", None)),
                "realized_vol_percentile": _finite(getattr(row, "realized_vol_percentile", None)),
                "drawdown_pressure": _finite(getattr(row, "drawdown_pressure", None)),
                "market_breadth_pressure": _finite(getattr(row, "market_breadth_pressure", None)),
                "turnover_stress": _finite(getattr(row, "turnover_stress", None)),
            },
        })

    gaps = _gap_rows(official_risk, index_history, realtime_status, estimated)
    return {
        "status": "ok",
        "official_latest_date": official_latest,
        "estimated_latest_date": rows[-1]["date"] if rows else None,
        "rows": rows,
        "gaps": gaps,
        "methodology": _methodology(),
    }


def _methodology() -> dict:
    return {
        "official_series": "risk_temperature.csv remains official close only and is not backfilled with weak daily option data.",
        "estimated_series": "Estimated rows use OK realtime AVIX when official daily AVIX is missing or rejected; missing QVIX may use exact-date realtime 300 index QVIX, else 300ETF QVIX as an explicit proxy.",
        "non_avix_factors": "Index-derived realized volatility, drawdown, and turnover use available close data; estimated rows fetch exact-date live stock breadth when official breadth lags.",
        "chart_rule": "The website renders official close as a solid line and estimated close as a dashed line.",
    }


def nowcast_rows_csv(payload: dict) -> pd.DataFrame:
    rows = payload.get("rows", [])
    if not rows:
        return pd.DataFrame(columns=[
            "trade_date", "risk_temperature_estimated", "regime", "regime_cn",
            "quality", "baseline_trade_date", "gap_reason", "avix_realtime_mid",
            "model_confidence",
        ])
    return pd.DataFrame(rows).loc[:, [
        "trade_date", "risk_temperature_estimated", "regime", "regime_cn",
        "quality", "baseline_trade_date", "gap_reason", "avix_realtime_mid",
        "model_confidence",
    ]]


def build_nowcast_history_from_files() -> dict:
    return build_nowcast_history(
        official_risk=read_csv(CALCULATED / "risk_components.csv"),
        official_clean=read_csv(CALCULATED / "avix_clean_close.csv"),
        qvix_raw=read_csv(RAW / "qvix" / "qvix.csv"),
        rate_curve=read_csv(NORMALIZED / "rate_curve_history.csv"),
        index_history=read_csv(NORMALIZED / "index_history.csv"),
        breadth_history=read_csv(NORMALIZED / "breadth_history.csv"),
    )
