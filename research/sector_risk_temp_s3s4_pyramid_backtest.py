#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import argparse
import json
import os
import time
import re

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "research" / "output"
RAW_CACHE = DATA / "raw" / "sectors"
NORMALIZED = DATA / "normalized"

INITIAL_BUY = 10_000.0
ADD_BUY = 2_000.0
DROP_STEP = 0.03
TAKE_PROFIT = 0.13
BUY_COST = 0.0003
SELL_COST = 0.0003
RISK_MIN = 73.0
RISK_MAX = 78.0
SIGNAL_LOOKBACK_DAYS = 10
TRADING_DAYS = 252
DEFAULT_START_DATE = "2023-12-23"
DEFAULT_END_DATE = "2026-07-02"
ETF_HOLD_YEARS = ["2026", "2025", "2024"]


SECTOR_ETF_KEYWORDS = {
    "航海装备": ["军工", "国防", "船舶", "航天军工", "军工龙头"],
    "航空装备": ["军工", "国防", "航空", "航天军工", "军工龙头"],
    "航天装备": ["军工", "国防", "航天", "航天军工", "军工龙头"],
    "地面兵装": ["军工", "国防", "兵工", "航天军工", "军工龙头"],
    "国防军工": ["军工", "国防", "航天军工", "军工龙头"],
    "汽车零部件": ["汽车", "智能汽车", "新能源汽车", "汽车零部件"],
    "摩托车及其他": ["汽车", "智能汽车"],
    "汽车": ["汽车", "智能汽车", "新能源汽车"],
    "电力设备": ["电池", "新能源", "光伏", "电力设备", "新能源车", "储能"],
    "电机": ["电力设备", "新能源", "机械"],
    "贵金属": ["黄金", "有色", "有色金属", "资源"],
    "小金属": ["有色", "有色金属", "稀有金属", "资源"],
    "有色金属": ["有色", "有色金属", "资源", "稀有金属"],
    "半导体": ["半导体", "芯片", "集成电路"],
    "消费电子": ["消费电子", "电子", "芯片"],
    "元件": ["电子", "半导体", "芯片"],
    "电子": ["电子", "半导体", "芯片", "消费电子"],
    "计算机设备": ["计算机", "软件", "人工智能", "云计算", "信息技术"],
    "计算机": ["计算机", "软件", "人工智能", "云计算", "信息技术"],
    "机械设备": ["机械", "高端装备", "机器人", "智能制造"],
    "一般零售": ["消费", "零售", "商贸"],
    "贸易": ["消费", "商贸", "一带一路"],
    "商贸零售": ["消费", "零售", "商贸"],
    "非银金融": ["证券", "金融", "非银", "券商"],
    "证券": ["证券", "券商", "非银", "金融"],
    "养殖业": ["畜牧", "养殖", "农业", "农牧"],
    "渔业": ["农业", "养殖", "农牧"],
    "农林牧渔": ["农业", "畜牧", "养殖", "农牧"],
    "医药生物": ["医药", "医疗", "生物医药", "创新药"],
    "化学制药": ["医药", "创新药", "生物医药"],
    "生物制品": ["生物医药", "医药", "创新药"],
    "建筑材料": ["建材", "水泥", "建筑材料"],
    "水泥": ["水泥", "建材", "建筑材料"],
    "美容护理": ["美容", "护理", "化妆品"],
    "个护用品": ["美容", "护理", "化妆品"],
    "纺织服饰": ["纺织", "服装", "消费"],
    "饰品": ["珠宝", "消费", "纺织服饰"],
    "家用电器": ["家电", "家用电器"],
    "软件开发": ["软件", "计算机", "人工智能", "云计算"],
    "IT服务": ["计算机", "软件", "信息技术", "人工智能"],
    "工程机械": ["工程机械", "机械", "高端装备"],
}


@dataclass
class Trade:
    universe: str
    symbol: str
    name: str
    parent_name: str
    entry_signal_date: str
    entry_date: str
    exit_signal_date: str | None
    exit_date: str | None
    holding_days: int
    buy_count: int
    add_count: int
    invested_amount: float
    avg_cost: float
    exit_value: float
    profit_amount: float
    return_rate: float
    max_drawdown_on_position: float
    status: str


def _sector_symbol(code: object) -> str:
    return str(code).strip().split(".")[0]


def clean_sector_name(name: object) -> str:
    text = "" if pd.isna(name) else str(name)
    text = text.replace("Ⅱ", "").replace("II", "")
    return re.sub(r"[（）()ＡＢＣABC]+", "", text).strip()


def load_signals(start_date: str, end_date: str) -> pd.DataFrame:
    risk = pd.read_csv(DATA / "calculated" / "risk_temperature.csv")
    strategy = pd.read_csv(DATA / "calculated" / "strategy_s3_s4.csv")
    risk["trade_date"] = pd.to_datetime(risk["trade_date"])
    strategy["trade_date"] = pd.to_datetime(strategy["trade_date"])
    signal_cols = ["s3_signal", "s4_signal", "s3_s4_signal", "s3_buy", "s4_buy", "s3_s4_buy"]
    for col in signal_cols:
        strategy[col] = strategy[col].fillna(False).astype(bool)
    strategy["s3s4_or_buy"] = strategy[signal_cols].any(axis=1)
    signals = risk[["trade_date", "risk_temperature"]].merge(
        strategy[["trade_date", "s3s4_or_buy"]], on="trade_date", how="left"
    )
    signals["s3s4_or_buy"] = signals["s3s4_or_buy"].fillna(False).astype(bool)
    signals = signals.sort_values("trade_date").reset_index(drop=True)
    signals["signal_in_10d"] = (
        signals["s3s4_or_buy"].rolling(SIGNAL_LOOKBACK_DAYS, min_periods=1).max().astype(bool)
    )
    signals["entry_signal"] = (
        signals["risk_temperature"].between(RISK_MIN, RISK_MAX, inclusive="both")
        & signals["signal_in_10d"]
    )
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    signals = signals[(signals["trade_date"] >= start) & (signals["trade_date"] <= end)].copy()
    return signals


def load_level1() -> pd.DataFrame:
    df = pd.read_csv(NORMALIZED / "sw_level1_sector_history.csv")
    df["universe"] = "SW_L1"
    df["parent_name"] = ""
    return normalize_sector_history(df)


def fetch_level2(sleep_seconds: float = 0.15) -> tuple[pd.DataFrame, pd.DataFrame]:
    os.environ.setdefault("NO_PROXY", "*")
    import akshare as ak

    info = ak.sw_index_second_info()
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, object]] = []
    fetched_at = pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds")
    for _, row in info.iterrows():
        symbol = _sector_symbol(row["行业代码"])
        name = str(row["行业名称"])
        parent = str(row.get("上级行业", ""))
        try:
            raw = ak.index_hist_sw(symbol=symbol, period="day")
            if raw is None or raw.empty:
                manifest.append({"symbol": symbol, "name": name, "parent_name": parent, "status": "EMPTY", "rows": 0, "error": ""})
                continue
            df = raw.rename(columns={
                "日期": "date",
                "代码": "symbol",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            }).copy()
            df["symbol"] = symbol
            df["name"] = name
            df["parent_name"] = parent
            df["universe"] = "SW_L2"
            df["source"] = "AKSHARE_SW_LEVEL2_INDEX"
            df["fetch_time"] = fetched_at
            frames.append(df[["date", "symbol", "name", "parent_name", "open", "close", "high", "low", "volume", "amount", "source", "fetch_time", "universe"]])
            manifest.append({"symbol": symbol, "name": name, "parent_name": parent, "status": "OK", "rows": len(df), "error": ""})
        except Exception as exc:  # noqa: BLE001
            manifest.append({"symbol": symbol, "name": name, "parent_name": parent, "status": "ERROR", "rows": 0, "error": str(exc)})
        if sleep_seconds:
            time.sleep(sleep_seconds)
    history = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return normalize_sector_history(history), pd.DataFrame(manifest)


def normalize_sector_history(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    for col in ["open", "close", "high", "low"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["symbol"] = out["symbol"].map(_sector_symbol)
    if "parent_name" not in out.columns:
        out["parent_name"] = ""
    if "universe" not in out.columns:
        out["universe"] = "SW_L1"
    out = out.dropna(subset=["date", "symbol", "name", "open", "close"])
    return out.sort_values(["universe", "symbol", "date"]).reset_index(drop=True)


def load_or_fetch_level2(no_fetch: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    path = NORMALIZED / "sw_level2_sector_history.csv"
    manifest_path = RAW_CACHE / "sw_level2_fetch_manifest.csv"
    if path.exists():
        cached = normalize_sector_history(pd.read_csv(path))
        manifest = pd.read_csv(manifest_path) if manifest_path.exists() else pd.DataFrame()
        return cached, manifest
    if no_fetch:
        return pd.DataFrame(), pd.DataFrame()
    RAW_CACHE.mkdir(parents=True, exist_ok=True)
    history, manifest = fetch_level2()
    if not history.empty:
        history.to_csv(path, index=False)
    if not manifest.empty:
        manifest.to_csv(manifest_path, index=False)
    return history, manifest


def etf_candidates() -> pd.DataFrame:
    cache = RAW_CACHE / "fund_name_em_etf_candidates.csv"
    if cache.exists():
        return pd.read_csv(cache, dtype={"fund_code": str})
    import akshare as ak

    os.environ.setdefault("NO_PROXY", "*")
    raw = ak.fund_name_em()
    df = raw.rename(columns={"基金代码": "fund_code", "基金简称": "fund_name", "基金类型": "fund_type"}).copy()
    df["fund_code"] = df["fund_code"].astype(str).str.zfill(6)
    df["fund_name"] = df["fund_name"].astype(str)
    df["fund_type"] = df["fund_type"].astype(str)
    mask = (
        df["fund_name"].str.contains("ETF", case=False, na=False)
        & ~df["fund_name"].str.contains("联接|连接|LOF|QDII|债|货币|REIT", case=False, na=False)
        & df["fund_type"].str.contains("指数型|股票", na=False)
    )
    out = df.loc[mask, ["fund_code", "fund_name", "fund_type"]].drop_duplicates("fund_code")
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(cache, index=False)
    return out


def etf_holdings(fund_code: str) -> pd.DataFrame:
    cache_dir = RAW_CACHE / "etf_holdings"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{fund_code}.csv"
    if path.exists():
        return pd.read_csv(path, dtype={"股票代码": str})
    import akshare as ak

    os.environ.setdefault("NO_PROXY", "*")
    df = pd.DataFrame()
    for year in ETF_HOLD_YEARS:
        try:
            df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
        except Exception:  # noqa: BLE001
            df = pd.DataFrame()
        if not df.empty:
            df["holding_query_year"] = year
            break
    if not df.empty:
        df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
    df.to_csv(path, index=False)
    time.sleep(0.05)
    return df


def sector_keywords(name: str, parent_name: str) -> list[str]:
    base = clean_sector_name(name)
    parent = clean_sector_name(parent_name)
    keys = [base]
    if parent:
        keys.append(parent)
    for source in [base, parent]:
        for k, vals in SECTOR_ETF_KEYWORDS.items():
            if k in source:
                keys.extend(vals)
    return list(dict.fromkeys([k for k in keys if k]))


def score_etf_name(fund_name: str, keywords: list[str]) -> float:
    text = clean_sector_name(fund_name)
    score = 0.0
    for i, key in enumerate(keywords):
        if not key:
            continue
        if key in text:
            score += max(8.0 - i * 0.5, 2.0)
        elif len(key) >= 3 and any(part in text for part in re.split(r"[、/ -]", key)):
            score += 1.5
    if "ETF" in fund_name.upper():
        score += 1.0
    if any(x in text for x in ["增强", "联接", "连接"]):
        score -= 5.0
    return score


def build_etf_mapping(summary: pd.DataFrame) -> pd.DataFrame:
    candidates = etf_candidates()
    rows: list[dict[str, object]] = []
    broad_fallback = candidates[candidates["fund_name"].str.contains("沪深300ETF|中证A500ETF|中证800ETF", na=False)]
    fallback = broad_fallback.iloc[0] if not broad_fallback.empty else candidates.iloc[0]

    for _, sector in summary[["universe", "symbol", "name", "parent_name"]].drop_duplicates().iterrows():
        keywords = sector_keywords(sector["name"], sector.get("parent_name", ""))
        scored = candidates.copy()
        scored["name_score"] = scored["fund_name"].map(lambda x: score_etf_name(str(x), keywords))
        scored = scored.sort_values(["name_score", "fund_name"], ascending=[False, True])
        top = scored[scored["name_score"] > 0].head(8)
        if top.empty:
            top = pd.DataFrame([fallback])
            top["name_score"] = 0.0

        best: dict[str, object] | None = None
        for _, etf in top.iterrows():
            holdings = etf_holdings(str(etf["fund_code"]))
            holding_count = int(holdings["股票代码"].nunique()) if not holdings.empty and "股票代码" in holdings.columns else 0
            top10_weight = float(pd.to_numeric(holdings.get("占净值比例", pd.Series(dtype=float)), errors="coerce").head(10).sum()) if not holdings.empty else np.nan
            score = float(etf["name_score"]) * 100 + min(holding_count, 200) + (top10_weight if np.isfinite(top10_weight) else 0)
            item = {
                "universe": sector["universe"],
                "symbol": sector["symbol"],
                "mapped_etf_code": str(etf["fund_code"]).zfill(6),
                "mapped_etf_name": str(etf["fund_name"]),
                "etf_name_score": float(etf["name_score"]),
                "etf_disclosed_stock_count": holding_count,
                "etf_top10_weight_pct": top10_weight,
                "etf_mapping_score": score,
                "etf_mapping_keywords": "|".join(keywords),
                "etf_mapping_method": "ETF name relevance + disclosed equity holding count",
            }
            if best is None or item["etf_mapping_score"] > best["etf_mapping_score"]:
                best = item
        rows.append(best or {
            "universe": sector["universe"],
            "symbol": sector["symbol"],
            "mapped_etf_code": "",
            "mapped_etf_name": "",
            "etf_name_score": 0.0,
            "etf_disclosed_stock_count": 0,
            "etf_top10_weight_pct": np.nan,
            "etf_mapping_score": 0.0,
            "etf_mapping_keywords": "|".join(keywords),
            "etf_mapping_method": "NO_MATCH",
        })
    return pd.DataFrame(rows)


def backtest_one_sector(sector: pd.DataFrame, signals: pd.DataFrame) -> list[Trade]:
    sector = sector.sort_values("date").reset_index(drop=True)
    merged = sector.merge(signals, left_on="date", right_on="trade_date", how="left")
    merged["entry_signal"] = merged["entry_signal"].fillna(False).astype(bool)
    merged = merged.dropna(subset=["open", "close"]).reset_index(drop=True)
    if len(merged) < 30:
        return []

    trades: list[Trade] = []
    in_pos = False
    shares = 0.0
    invested = 0.0
    last_buy_trigger = np.nan
    entry_signal_i = -1
    entry_i = -1
    max_drawdown = 0.0
    buy_count = 0
    add_count = 0
    avg_cost = np.nan

    for i in range(len(merged) - 1):
        close = float(merged.loc[i, "close"])
        next_open = float(merged.loc[i + 1, "open"])
        if not np.isfinite(close) or not np.isfinite(next_open):
            continue

        if not in_pos:
            if bool(merged.loc[i, "entry_signal"]):
                px = next_open * (1 + BUY_COST)
                shares = INITIAL_BUY / px
                invested = INITIAL_BUY
                last_buy_trigger = px
                avg_cost = px
                entry_signal_i = i
                entry_i = i + 1
                max_drawdown = 0.0
                buy_count = 1
                add_count = 0
                in_pos = True
            continue

        close_value = shares * close
        close_ret = close_value / invested - 1
        max_drawdown = min(max_drawdown, float(close_ret))

        if close_ret >= TAKE_PROFIT:
            exit_value = shares * next_open * (1 - SELL_COST)
            profit = exit_value - invested
            trades.append(Trade(
                universe=str(merged.loc[i, "universe"]),
                symbol=str(merged.loc[i, "symbol"]),
                name=str(merged.loc[i, "name"]),
                parent_name=str(merged.loc[i, "parent_name"]),
                entry_signal_date=pd.Timestamp(merged.loc[entry_signal_i, "date"]).strftime("%Y-%m-%d"),
                entry_date=pd.Timestamp(merged.loc[entry_i, "date"]).strftime("%Y-%m-%d"),
                exit_signal_date=pd.Timestamp(merged.loc[i, "date"]).strftime("%Y-%m-%d"),
                exit_date=pd.Timestamp(merged.loc[i + 1, "date"]).strftime("%Y-%m-%d"),
                holding_days=int(i + 1 - entry_i),
                buy_count=buy_count,
                add_count=add_count,
                invested_amount=float(invested),
                avg_cost=float(avg_cost),
                exit_value=float(exit_value),
                profit_amount=float(profit),
                return_rate=float(profit / invested),
                max_drawdown_on_position=float(max_drawdown),
                status="closed",
            ))
            in_pos = False
            shares = 0.0
            invested = 0.0
            continue

        if close <= last_buy_trigger * (1 - DROP_STEP):
            add_px = next_open * (1 + BUY_COST)
            shares += ADD_BUY / add_px
            invested += ADD_BUY
            avg_cost = invested / shares
            last_buy_trigger = add_px
            buy_count += 1
            add_count += 1

    if in_pos:
        i = len(merged) - 1
        close = float(merged.loc[i, "close"])
        exit_value = shares * close * (1 - SELL_COST)
        profit = exit_value - invested
        trades.append(Trade(
            universe=str(merged.loc[i, "universe"]),
            symbol=str(merged.loc[i, "symbol"]),
            name=str(merged.loc[i, "name"]),
            parent_name=str(merged.loc[i, "parent_name"]),
            entry_signal_date=pd.Timestamp(merged.loc[entry_signal_i, "date"]).strftime("%Y-%m-%d"),
            entry_date=pd.Timestamp(merged.loc[entry_i, "date"]).strftime("%Y-%m-%d"),
            exit_signal_date=None,
            exit_date=None,
            holding_days=int(i - entry_i),
            buy_count=buy_count,
            add_count=add_count,
            invested_amount=float(invested),
            avg_cost=float(avg_cost),
            exit_value=float(exit_value),
            profit_amount=float(profit),
            return_rate=float(profit / invested),
            max_drawdown_on_position=float(max_drawdown),
            status="open",
        ))
    return trades


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for keys, group in trades.groupby(["universe", "symbol", "name", "parent_name"], dropna=False):
        universe, symbol, name, parent_name = keys
        closed = group[group["status"] == "closed"]
        open_trades = group[group["status"] == "open"]
        wins = closed[closed["profit_amount"] > 0]
        losses = closed[closed["profit_amount"] <= 0]
        first_entry = group["entry_date"].min()
        last_exit = closed["exit_date"].max() if not closed.empty else None
        total_profit_closed = float(closed["profit_amount"].sum())
        total_profit_all = float(group["profit_amount"].sum())
        total_invested = float(group["invested_amount"].sum())
        max_single_invested = float(group["invested_amount"].max()) if not group.empty else 0.0
        rows.append({
            "universe": universe,
            "symbol": symbol,
            "name": name,
            "parent_name": parent_name,
            "trade_count_total": int(len(group)),
            "trade_count_closed": int(len(closed)),
            "open_trade_count": int(len(open_trades)),
            "win_count_closed": int(len(wins)),
            "loss_count_closed": int(len(losses)),
            "win_rate_closed": float(len(wins) / len(closed)) if len(closed) else np.nan,
            "total_invested_amount": total_invested,
            "max_single_trade_invested": max_single_invested,
            "avg_invested_per_trade": float(group["invested_amount"].mean()),
            "total_profit_closed": total_profit_closed,
            "total_profit_including_open": total_profit_all,
            "avg_profit_closed": float(closed["profit_amount"].mean()) if len(closed) else np.nan,
            "median_profit_closed": float(closed["profit_amount"].median()) if len(closed) else np.nan,
            "avg_return_closed": float(closed["return_rate"].mean()) if len(closed) else np.nan,
            "median_return_closed": float(closed["return_rate"].median()) if len(closed) else np.nan,
            "best_trade_return": float(closed["return_rate"].max()) if len(closed) else np.nan,
            "worst_trade_return": float(closed["return_rate"].min()) if len(closed) else np.nan,
            "worst_position_drawdown": float(group["max_drawdown_on_position"].min()),
            "avg_holding_days_closed": float(closed["holding_days"].mean()) if len(closed) else np.nan,
            "median_holding_days_closed": float(closed["holding_days"].median()) if len(closed) else np.nan,
            "max_holding_days": int(group["holding_days"].max()),
            "avg_add_count": float(group["add_count"].mean()),
            "max_add_count": int(group["add_count"].max()),
            "first_entry_date": first_entry,
            "last_closed_exit_date": last_exit,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["profit_per_10k_invested"] = out["total_profit_including_open"] / out["total_invested_amount"] * 10_000
    out["ranking_score"] = (
        out["total_profit_including_open"].rank(ascending=False, pct=True)
        + out["win_rate_closed"].fillna(0).rank(ascending=False, pct=True)
        + out["trade_count_closed"].clip(upper=8).rank(ascending=False, pct=True)
        - out["worst_position_drawdown"].abs().rank(ascending=True, pct=True) * 0.35
    )
    return out.sort_values(
        ["total_profit_including_open", "win_rate_closed", "trade_count_closed"],
        ascending=[False, False, False],
    )


def write_report(summary: pd.DataFrame, trades: pd.DataFrame, manifest: pd.DataFrame, start_date: str, end_date: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mapping = build_etf_mapping(summary)
    summary = summary.merge(mapping, on=["universe", "symbol"], how="left")
    top_profit = summary.head(20).copy()
    top_win = summary[summary["trade_count_closed"] >= 2].sort_values(
        ["win_rate_closed", "total_profit_including_open", "trade_count_closed"],
        ascending=[False, False, False],
    ).head(20)
    best_score = summary.sort_values("ranking_score", ascending=False).head(20)

    summary.to_csv(OUT / "sector_risk_temp_s3s4_pyramid_summary.csv", index=False)
    trades.to_csv(OUT / "sector_risk_temp_s3s4_pyramid_trades.csv", index=False)
    mapping.to_csv(OUT / "sector_risk_temp_s3s4_etf_mapping.csv", index=False)
    top_profit.to_csv(OUT / "sector_risk_temp_s3s4_pyramid_top20_profit.csv", index=False)
    top_win.to_csv(OUT / "sector_risk_temp_s3s4_pyramid_top20_winrate.csv", index=False)
    best_score.to_csv(OUT / "sector_risk_temp_s3s4_pyramid_top20_score.csv", index=False)
    if not manifest.empty:
        manifest.to_csv(OUT / "sector_risk_temp_s3s4_level2_fetch_manifest.csv", index=False)

    report = {
        "rules": {
            "risk_temperature_range": [RISK_MIN, RISK_MAX],
            "signal_lookback_trading_days": SIGNAL_LOOKBACK_DAYS,
            "initial_buy": INITIAL_BUY,
            "add_buy": ADD_BUY,
            "drop_step": DROP_STEP,
            "take_profit": TAKE_PROFIT,
            "buy_cost": BUY_COST,
            "sell_cost": SELL_COST,
            "execution": "T close signal, T+1 open execution",
            "sample_start": start_date,
            "sample_end": end_date,
            "etf_mapping": "ETF name relevance + disclosed equity holding count; official SW component overlap was unavailable because AkShare SW component endpoint failed for sector indexes.",
        },
        "sample": {
            "sector_count": int(summary.shape[0]),
            "trade_count_total": int(summary["trade_count_total"].sum()),
            "trade_count_closed": int(summary["trade_count_closed"].sum()),
            "universe_counts": summary["universe"].value_counts().to_dict(),
        },
        "outputs": [
            "sector_risk_temp_s3s4_pyramid_summary.csv",
            "sector_risk_temp_s3s4_pyramid_trades.csv",
            "sector_risk_temp_s3s4_pyramid_top20_profit.csv",
            "sector_risk_temp_s3s4_pyramid_top20_winrate.csv",
            "sector_risk_temp_s3s4_pyramid_top20_score.csv",
            "sector_risk_temp_s3s4_etf_mapping.csv",
        ],
    }
    (OUT / "sector_risk_temp_s3s4_pyramid_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md = [
        "# 风险温度 + S3/S4 板块金字塔策略回测",
        "",
        "## 回测口径",
        f"- 样本区间: {start_date} 至 {end_date}",
        f"- 条件: risk_temperature 在 {RISK_MIN:.0f}-{RISK_MAX:.0f}，且过去 {SIGNAL_LOOKBACK_DAYS} 个交易日内出现过 S3/S4 信号或买点。",
        "- 执行: T 日收盘确认信号，T+1 开盘成交。",
        f"- 初始买入: {INITIAL_BUY:.0f} 元；每从上一次买入触发价下跌 {DROP_STEP:.0%}，T+1 开盘加仓 {ADD_BUY:.0f} 元。",
        f"- 止盈: 整体持仓收益达到 {TAKE_PROFIT:.0%}，T+1 开盘全部卖出。",
        f"- 成本: 买入 {BUY_COST:.2%}，卖出 {SELL_COST:.2%}。",
        "- ETF映射: 优先按板块/上级行业关键词匹配 ETF 名称，再用 ETF 最新披露股票持仓数量和前十大权重校验；输出保留映射分数和关键词。",
        "",
        "## 样本概况",
        f"- 覆盖板块数: {summary.shape[0]}",
        f"- 总交易次数: {int(summary['trade_count_total'].sum())}",
        f"- 已完成交易次数: {int(summary['trade_count_closed'].sum())}",
        "",
        "## 收益前20",
        top_profit.to_markdown(index=False),
        "",
        "## 胜率前20（至少2笔已完成交易）",
        top_win.to_markdown(index=False),
    ]
    (OUT / "sector_risk_temp_s3s4_pyramid_report.md").write_text("\n".join(md), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch-level2", action="store_true")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    args = parser.parse_args()

    os.environ.setdefault("NO_PROXY", "*")
    signals = load_signals(args.start_date, args.end_date)
    level1 = load_level1()
    level2, manifest = load_or_fetch_level2(args.no_fetch_level2)
    sectors = pd.concat([level1, level2], ignore_index=True) if not level2.empty else level1

    all_trades: list[dict[str, object]] = []
    for _, sector in sectors.groupby(["universe", "symbol"], sort=False):
        all_trades.extend(asdict(t) for t in backtest_one_sector(sector, signals))

    trades = pd.DataFrame(all_trades)
    if trades.empty:
        raise SystemExit("No trades generated. Check signal/risk conditions.")
    summary = summarize(trades)
    write_report(summary, trades, manifest, args.start_date, args.end_date)
    print(
        "Sector pyramid backtest complete: "
        f"sectors={summary.shape[0]} trades={len(trades)} closed={(trades['status'] == 'closed').sum()} "
        f"top_profit={summary.iloc[0]['universe']} {summary.iloc[0]['name']} "
        f"{summary.iloc[0]['total_profit_including_open']:.2f}"
    )


if __name__ == "__main__":
    main()
