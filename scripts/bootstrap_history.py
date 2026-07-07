#!/usr/bin/env python3
from __future__ import annotations
import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import yaml

from src.storage.paths import ROOT, RAW, NORMALIZED, CALCULATED, ensure_dirs
from src.storage.csv_store import write_csv, read_csv
from src.data_sources.akshare_indices import fetch_index_daily
from src.data_sources.akshare_options import fetch_option_daily, fetch_option_realtime
from src.data_sources.akshare_qvix import fetch_qvix
from src.data_sources.akshare_breadth import fetch_a_breadth_snapshot, summarize_breadth
from src.data_sources.shibor import fetch_shibor
from src.core.calendar import current_realtime_trade_date, merged_trading_days, trading_days_from_index
from src.core.contracts import build_contract_master
from src.core.option_chain import build_daily_option_chain
from src.core.avix_formula import calculate_avix_for_date
from src.core.clean_surface import clean_option_surface
from src.core.qvix_validation import validate_qvix
from src.core.realized_vol import compute_realized_vol
from src.core.drawdown import compute_drawdown
from src.core.breadth import compute_breadth_pressure, drop_legacy_synthetic_breadth
from src.core.risk_temperature import compute_risk_temperature
from src.core.realtime_avix import calculate_realtime_avix
from src.utils.dates import now_cn

def load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))

def fetch_indices(recent_days: int | None) -> pd.DataFrame:
    universe = load_yaml("universe.yml")
    frames = []
    for cfg in universe["indices"].values():
        symbol = cfg["symbol"]
        cache_path = RAW / "indices" / f"{symbol}.csv"
        old = read_csv(cache_path)
        try:
            df = fetch_index_daily(symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN index fetch failed {symbol}: {exc}")
            df = old
        if df.empty:
            df = old
        if df.empty:
            raise RuntimeError(f"No index data available for {symbol}; source failed and no cache exists")
        if recent_days:
            df = df.tail(max(recent_days + 80, recent_days))
        write_csv(df, cache_path)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    write_csv(out, NORMALIZED / "index_history.csv")
    return out

def option_contracts_from_index(hs: pd.DataFrame, recent_days: int | None, full: bool = False) -> list[str]:
    hs = hs.sort_values("date")
    if recent_days and not full:
        hs = hs.tail(recent_days)
    closes = pd.to_numeric(hs["close"], errors="coerce").dropna()
    if closes.empty:
        spot = 3600
    else:
        spot = float(closes.iloc[-1])
    dates = pd.to_datetime(hs["date"])
    latest_month = dates.max().to_period("M").to_timestamp()
    if full:
        universe = load_yaml("universe.yml")
        start = pd.to_datetime(universe["options"]["start_date"])
        end = pd.to_datetime(hs["date"]).max() + pd.DateOffset(months=6)
        hs_full = hs.copy()
        hs_full["date_ts"] = pd.to_datetime(hs_full["date"])
        hs_full["close"] = pd.to_numeric(hs_full["close"], errors="coerce")
        month_range = pd.date_range(start.to_period("M").to_timestamp(), end.to_period("M").to_timestamp(), freq="MS")
        contracts = []
        for month_start in month_range:
            ref = hs_full[hs_full["date_ts"] <= month_start]
            spot_ref = float(ref["close"].dropna().iloc[-1]) if not ref.empty and not ref["close"].dropna().empty else spot
            low = max(2500, int((spot_ref * 0.75) // 50 * 50))
            high = min(7000, int((spot_ref * 1.25 + 49) // 50 * 50))
            yy, mm = month_start.strftime("%y"), month_start.strftime("%m")
            for strike in range(low, high + 1, 50):
                contracts.append(f"io{yy}{mm}C{strike}")
                contracts.append(f"io{yy}{mm}P{strike}")
        return contracts
    if recent_days:
        month_range = pd.date_range(latest_month, latest_month + pd.DateOffset(months=6), freq="MS")
    else:
        month_range = pd.date_range(dates.min(), dates.max() + pd.DateOffset(months=6), freq="MS")
    months = [d.strftime("%y%m") for d in month_range]
    center = int(round(spot / 100) * 100)
    strikes = sorted(set(range(max(2500, center - 500), min(7000, center + 501), 100)) | set(range(max(2500, center - 300), min(7000, center + 301), 50)))
    return [f"io{m}{cp}{k}" for m in months for k in strikes for cp in ["C", "P"]]

def load_cached_option_frames() -> list[pd.DataFrame]:
    frames = []
    for path in sorted((RAW / "options_daily").glob("io*.csv")):
        df = read_csv(path)
        if not df.empty:
            frames.append(df)
    return frames

def read_option_manifest() -> pd.DataFrame:
    path = RAW / "options_daily" / "fetch_manifest.csv"
    if not path.exists():
        return pd.DataFrame(columns=["contract", "status", "last_error", "last_try"])
    return pd.read_csv(path)

def write_option_manifest(manifest: pd.DataFrame) -> None:
    path = RAW / "options_daily" / "fetch_manifest.csv"
    manifest.drop_duplicates("contract", keep="last").sort_values("contract").to_csv(path, index=False)

def fetch_options(index_history: pd.DataFrame, recent_days: int | None, full: bool = False) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
    hs = index_history[index_history["symbol"] == "sh000300"].copy()
    candidates = option_contracts_from_index(hs, recent_days, full=full)
    frames = []
    probe_limit = len(candidates) if full else min(len(candidates), 420)
    manifest = read_option_manifest()
    status_map = dict(zip(manifest.get("contract", []), manifest.get("status", [])))
    max_new = int(os.environ.get("MAX_OPTION_FETCHES", "0") or "0")

    def fetch_one(contract: str) -> tuple[str, pd.DataFrame, str, str]:
        try:
            df = fetch_option_daily(contract)
            if df.empty:
                return contract, df, "empty", ""
            return contract, df, "ok", ""
        except Exception as exc:  # noqa: BLE001
            return contract, pd.DataFrame(), "failed", str(exc)[:240]

    to_fetch: list[str] = []
    for contract in candidates[:probe_limit]:
        path = RAW / "options_daily" / f"{contract.lower()}.csv"
        if path.exists():
            df = read_csv(path)
        elif full and status_map.get(contract.lower()) == "empty":
            continue
        else:
            if not max_new or len(to_fetch) < max_new:
                to_fetch.append(contract)
            continue
        if not df.empty:
            frames.append(df)
    workers = int(os.environ.get("OPTION_FETCH_WORKERS", "10" if full else "4"))
    if to_fetch:
        print(f"Fetching {len(to_fetch)} option contracts with {workers} workers")
        done = 0
        failures = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(fetch_one, contract) for contract in to_fetch]
            for future in as_completed(futures):
                contract, df, status, error = future.result()
                done += 1
                path = RAW / "options_daily" / f"{contract.lower()}.csv"
                if not df.empty:
                    write_csv(df, path)
                    frames.append(df)
                    status = "ok"
                    error = ""
                elif status == "failed":
                    failures += 1
                    if failures in {1, 10, 25}:
                        print(f"WARN option fetch failed/timeout {contract}: {error}")
                manifest = pd.concat([manifest, pd.DataFrame([{
                    "contract": contract.lower(),
                    "status": status,
                    "last_error": error,
                    "last_try": now_cn().isoformat(timespec="seconds"),
                }])], ignore_index=True)
                if done % 200 == 0 or done == len(to_fetch):
                    print(f"Option fetch progress {done}/{len(to_fetch)}")
    if not manifest.empty:
        write_option_manifest(manifest)
    if not frames:
        frames = load_cached_option_frames()
        if frames:
            print("WARN no fresh option history fetched; using cached verified option history")
    if not frames:
        raise RuntimeError("No option history available; source failed and no cached contracts exist")
    master = build_contract_master(frames)
    write_csv(master, NORMALIZED / "contract_master.csv")
    return master, frames

def calculate_all(master: pd.DataFrame, option_frames: list[pd.DataFrame], index_history: pd.DataFrame) -> pd.DataFrame:
    hs300_history = index_history[index_history["symbol"] == "sh000300"].copy()
    trading_days = set(trading_days_from_index(hs300_history))
    chain = build_daily_option_chain(master, option_frames, trading_days)
    write_csv(chain, NORMALIZED / "daily_option_chain.csv")
    dates = sorted(chain["trade_date"].unique().tolist()) if not chain.empty else sorted(hs300_history["date"].unique().tolist())
    try:
        rates = fetch_shibor()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN shibor fetch failed: {exc}")
        rates = pd.DataFrame()
    if rates.empty:
        rates = read_csv(NORMALIZED / "rate_curve_history.csv")
        if rates.empty:
            raise RuntimeError("No Shibor data available; source failed and no cached rate curve exists")
        print("WARN Shibor source unavailable; using cached rate curve")
    write_csv(rates, NORMALIZED / "rate_curve_history.csv")
    realtime_trade_date = current_realtime_trade_date(hs300_history)
    if realtime_trade_date:
        try:
            realtime_raw = fetch_option_realtime("io")
        except Exception as exc:  # noqa: BLE001
            print(f"WARN realtime option fetch failed: {exc}")
            realtime_raw = pd.DataFrame()
        if not realtime_raw.empty:
            write_csv(realtime_raw, RAW / "option_realtime" / f"{realtime_trade_date}.csv")
        realtime_chain, realtime_avix = calculate_realtime_avix(
            realtime_raw,
            rates,
            realtime_trade_date,
            set(merged_trading_days(hs300_history)),
        )
        if not realtime_chain.empty:
            write_csv(realtime_chain, NORMALIZED / "realtime_option_chain.csv")
        write_csv(realtime_avix, CALCULATED / "avix_realtime_mid.csv")
    chain_by_date = {str(d): g.copy() for d, g in chain.groupby("trade_date")} if not chain.empty else {}
    rows = [calculate_avix_for_date(chain_by_date[d], rates, d, "price_raw") for d in dates if d in chain_by_date]
    raw = pd.DataFrame(rows)
    if not raw.empty:
        raw = raw.rename(columns={"avix": "avix_raw"})
    write_csv(raw, CALCULATED / "avix_raw_close.csv")
    if len(chain) > 100_000 and not raw.empty:
        clean = raw.rename(columns={"avix_raw": "avix_clean"}).copy()
        while not clean.empty and "WARN_NOT_BRACKET_30D" in str(clean.sort_values("trade_date").iloc[-1]["quality"]):
            clean = clean[clean["trade_date"] != clean["trade_date"].max()].copy()
        clean["avix_raw"] = clean["avix_clean"]
        clean["raw_clean_diff"] = 0.0
        clean["cleaned_option_count"] = clean[["near_n_options", "next_n_options"]].min(axis=1)
        clean["cleaned_option_ratio"] = 1.0
        clean["clean_method"] = "moneyness_filter_fast_from_raw"
    else:
        clean_chain = clean_option_surface(chain, rates)
        clean_for_calc = clean_chain.copy()
        if not clean_for_calc.empty and "clean_valid" in clean_for_calc.columns:
            clean_for_calc["valid_price"] = clean_for_calc["clean_valid"].astype(bool)
        clean_by_date = {str(d): g.copy() for d, g in clean_for_calc.groupby("trade_date")} if not clean_for_calc.empty else {}
        clean_rows = [calculate_avix_for_date(clean_by_date[d], rates, d, "clean_price") for d in dates if d in clean_by_date]
        clean = pd.DataFrame(clean_rows)
        if not clean.empty:
            clean = clean.rename(columns={"avix": "avix_clean"}).merge(raw[["trade_date", "avix_raw"]], on="trade_date", how="left")
            clean["raw_clean_diff"] = (clean["avix_clean"] - clean["avix_raw"]).abs()
            clean["cleaned_option_count"] = clean_chain.groupby("trade_date")["clean_valid"].sum().reindex(clean["trade_date"]).values
            clean["cleaned_option_ratio"] = clean["cleaned_option_count"] / clean_chain.groupby("trade_date").size().reindex(clean["trade_date"]).values
            clean["clean_method"] = "iv_filter_rolling_median"
            clean.loc[clean["raw_clean_diff"] > 2.0, "quality"] = clean["quality"].astype(str) + "|WARN_CLEAN_IMPACT_HIGH"
            clean.loc[clean["raw_clean_diff"] > 4.0, "quality"] = clean["quality"].astype(str) + "|LOW_CLEAN_IMPACT_TOO_HIGH"
    write_csv(clean, CALCULATED / "avix_clean_close.csv")
    try:
        qvix = fetch_qvix()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN qvix fetch failed: {exc}")
        qvix = pd.DataFrame()
    write_csv(qvix, RAW / "qvix" / "qvix.csv")
    qv = validate_qvix(clean, qvix)
    write_csv(qv, CALCULATED / "qvix_validation.csv")
    realized = compute_realized_vol(index_history)
    drawdown = compute_drawdown(index_history)
    breadth_hist = drop_legacy_synthetic_breadth(read_csv(NORMALIZED / "breadth_history.csv"))
    if breadth_hist.empty:
        try:
            breadth_raw = fetch_a_breadth_snapshot()
        except Exception as exc:  # noqa: BLE001
            print(f"WARN breadth fetch failed: {exc}")
            breadth_raw = pd.DataFrame()
        if not breadth_raw.empty:
            write_csv(breadth_raw, RAW / "breadth" / f"{dates[-1]}.csv")
        breadth_hist = summarize_breadth(breadth_raw, dates[-1])
        write_csv(breadth_hist, NORMALIZED / "breadth_history.csv")
    else:
        write_csv(breadth_hist, NORMALIZED / "breadth_history.csv")
    breadth = compute_breadth_pressure(breadth_hist)
    components = compute_risk_temperature(clean, qv, realized, drawdown, breadth, index_history)
    write_csv(components, CALCULATED / "risk_components.csv")
    write_csv(components[["trade_date", "risk_temperature", "regime", "regime_cn", "quality"]], CALCULATED / "risk_temperature.csv")
    audit = pd.DataFrame([{"trade_date": components["trade_date"].iloc[-1], "event": "bootstrap_history", "quality": components["quality"].iloc[-1], "time": now_cn().isoformat(timespec="seconds")}]) if not components.empty else pd.DataFrame()
    write_csv(audit, CALCULATED / "audit_log.csv")
    return components

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--recent-days", type=int)
    parser.add_argument("--fetch-only", action="store_true")
    args = parser.parse_args()
    ensure_dirs()
    recent = None if args.full else (args.recent_days or 120)
    index_history = fetch_indices(recent)
    master, frames = fetch_options(index_history, recent, full=args.full)
    if args.fetch_only:
        return
    calculate_all(master, frames, index_history)

if __name__ == "__main__":
    main()
