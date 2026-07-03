#!/usr/bin/env python3
from __future__ import annotations
import argparse
from datetime import timedelta
import math
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import yaml

from src.storage.paths import ROOT, RAW, NORMALIZED, CALCULATED, ensure_dirs
from src.storage.csv_store import write_csv, read_csv
from src.data_sources.akshare_indices import fetch_index_daily
from src.data_sources.akshare_options import fetch_option_daily
from src.data_sources.akshare_qvix import fetch_qvix
from src.data_sources.akshare_breadth import fetch_a_breadth_snapshot, summarize_breadth
from src.data_sources.shibor import fetch_shibor, fallback_rate_curve
from src.core.calendar import trading_days_from_index
from src.core.contracts import build_contract_master
from src.core.option_chain import build_daily_option_chain
from src.core.avix_formula import calculate_avix_for_date, black76_price
from src.core.clean_surface import clean_option_surface
from src.core.qvix_validation import validate_qvix
from src.core.realized_vol import compute_realized_vol
from src.core.drawdown import compute_drawdown
from src.core.breadth import compute_breadth_pressure
from src.core.risk_temperature import compute_risk_temperature
from src.utils.dates import now_cn

class TimeoutError(RuntimeError):
    pass

def with_timeout(seconds: int, fn, *args, **kwargs):
    def handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"timed out after {seconds}s")
    old = signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)
    try:
        return fn(*args, **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)

def load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))

def synthetic_index(symbol: str, days: int = 260) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    base = {"sh000300": 3600, "sh000001": 3000, "sh000905": 5500, "sh000852": 5800, "sz399006": 1900}.get(symbol, 3000)
    x = np.linspace(0, 8, len(dates))
    close = base * (1 + 0.04 * np.sin(x) + np.linspace(-0.03, 0.04, len(dates)))
    return pd.DataFrame({
        "date": dates.date.astype(str), "open": close * 0.997, "close": close,
        "high": close * 1.01, "low": close * 0.99, "volume": 1_000_000 + 100_000 * np.sin(x),
        "symbol": symbol, "source": "SYNTHETIC_FALLBACK", "fetch_time": now_cn().isoformat(timespec="seconds"),
    })

def fetch_indices(recent_days: int | None) -> pd.DataFrame:
    universe = load_yaml("universe.yml")
    frames = []
    for cfg in universe["indices"].values():
        symbol = cfg["symbol"]
        try:
            df = fetch_index_daily(symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN index fetch failed {symbol}: {exc}")
            df = synthetic_index(symbol)
        if df.empty:
            df = synthetic_index(symbol)
        if recent_days:
            df = df.tail(max(recent_days + 80, recent_days))
        write_csv(df, RAW / "indices" / f"{symbol}.csv")
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    write_csv(out, NORMALIZED / "index_history.csv")
    return out

def option_contracts_from_index(hs: pd.DataFrame, recent_days: int | None) -> list[str]:
    hs = hs.sort_values("date")
    if recent_days:
        hs = hs.tail(recent_days)
    closes = pd.to_numeric(hs["close"], errors="coerce").dropna()
    if closes.empty:
        spot = 3600
    else:
        spot = float(closes.iloc[-1])
    dates = pd.to_datetime(hs["date"])
    latest_month = dates.max().to_period("M").to_timestamp()
    if recent_days:
        month_range = pd.date_range(latest_month, latest_month + pd.DateOffset(months=6), freq="MS")
    else:
        month_range = pd.date_range(dates.min(), dates.max() + pd.DateOffset(months=6), freq="MS")
    months = [d.strftime("%y%m") for d in month_range]
    center = int(round(spot / 100) * 100)
    strikes = sorted(set(range(max(2500, center - 500), min(7000, center + 501), 100)) | set(range(max(2500, center - 300), min(7000, center + 301), 50)))
    return [f"io{m}{cp}{k}" for m in months for k in strikes for cp in ["C", "P"]]

def synthetic_options(hs: pd.DataFrame, contracts: list[str]) -> list[pd.DataFrame]:
    rows = []
    hs = hs.sort_values("date").tail(160).copy()
    hs["close"] = pd.to_numeric(hs["close"], errors="coerce")
    for contract in contracts[:80]:
        yy, mm, cp, strike = contract[2:4], contract[4:6], contract[6], int(contract[7:])
        expiry = pd.Timestamp(int("20" + yy), int(mm), 1) + pd.offsets.WeekOfMonth(week=2, weekday=4)
        term_rows = []
        for r in hs.itertuples():
            dte = (expiry.date() - pd.to_datetime(r.date).date()).days
            if 7 <= dte <= 180:
                f = float(r.close)
                sigma = 0.22 + 0.08 * abs(math.log(strike / f))
                price = black76_price(f, strike, dte / 365, 0.02, sigma, cp)
                term_rows.append({
                    "date": r.date, "open": price * 0.98, "high": price * 1.04, "low": price * 0.95, "close": price,
                    "volume": 100, "contract": contract.lower(), "month": f"20{yy}-{mm}", "cp": cp, "strike": strike,
                    "source": "SYNTHETIC_FALLBACK", "fetch_time": now_cn().isoformat(timespec="seconds"),
                })
        if term_rows:
            rows.append(pd.DataFrame(term_rows))
    return rows

def fetch_options(index_history: pd.DataFrame, recent_days: int | None) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
    hs = index_history[index_history["symbol"] == "sh000300"].copy()
    candidates = option_contracts_from_index(hs, recent_days)
    frames = []
    probe_limit = 48 if recent_days else 240
    fail_count = 0
    for contract in candidates[:probe_limit]:
        path = RAW / "options_daily" / f"{contract.lower()}.csv"
        if path.exists():
            df = read_csv(path)
        else:
            try:
                df = with_timeout(8, fetch_option_daily, contract)
            except Exception as exc:  # noqa: BLE001
                fail_count += 1
                if fail_count in {1, 10, 25}:
                    print(f"WARN option fetch failed/timeout {contract}: {exc}")
                df = pd.DataFrame()
            if not df.empty:
                write_csv(df, path)
        if not df.empty:
            frames.append(df)
        if recent_days and len(frames) >= 24:
            break
    if not frames:
        print("WARN no verified option history fetched; writing synthetic fallback for local smokeability")
        frames = synthetic_options(hs, candidates)
        for df in frames:
            write_csv(df, RAW / "options_daily" / f"{df['contract'].iloc[0]}.csv")
    master = build_contract_master(frames)
    write_csv(master, NORMALIZED / "contract_master.csv")
    return master, frames

def calculate_all(master: pd.DataFrame, option_frames: list[pd.DataFrame], index_history: pd.DataFrame) -> pd.DataFrame:
    trading_days = set(trading_days_from_index(index_history[index_history["symbol"] == "sh000300"]))
    chain = build_daily_option_chain(master, option_frames, trading_days)
    write_csv(chain, NORMALIZED / "daily_option_chain.csv")
    dates = sorted(chain["trade_date"].unique().tolist()) if not chain.empty else sorted(index_history[index_history["symbol"] == "sh000300"]["date"].unique().tolist())
    try:
        rates = fetch_shibor()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN shibor fetch failed: {exc}")
        rates = pd.DataFrame()
    if rates.empty:
        rates = fallback_rate_curve(dates)
    write_csv(rates, NORMALIZED / "rate_curve_history.csv")
    rows = [calculate_avix_for_date(chain, rates, d, "price_raw") for d in dates] if not chain.empty else []
    raw = pd.DataFrame(rows)
    if not raw.empty:
        raw = raw.rename(columns={"avix": "avix_raw"})
    write_csv(raw, CALCULATED / "avix_raw_close.csv")
    clean_chain = clean_option_surface(chain, rates)
    clean_for_calc = clean_chain.copy()
    if not clean_for_calc.empty and "clean_valid" in clean_for_calc.columns:
        clean_for_calc["valid_price"] = clean_for_calc["clean_valid"].astype(bool)
    clean_rows = [calculate_avix_for_date(clean_for_calc, rates, d, "clean_price") for d in dates] if not clean_for_calc.empty else []
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
    breadth_hist = read_csv(NORMALIZED / "breadth_history.csv")
    if breadth_hist.empty:
        breadth_hist = pd.DataFrame([{"trade_date": dates[-1], "advancing_ratio": 0.5, "decline_ratio": 0.5, "big_down_ratio": 0.0, "limit_down_ratio": 0.0, "quality": "WARN_BREADTH_MISSING"}])
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
    args = parser.parse_args()
    ensure_dirs()
    recent = None if args.full else (args.recent_days or 120)
    index_history = fetch_indices(recent)
    master, frames = fetch_options(index_history, recent)
    calculate_all(master, frames, index_history)

if __name__ == "__main__":
    main()
