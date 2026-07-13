from __future__ import annotations
import pandas as pd
from src.utils.config import load_thresholds

_THRESHOLDS = load_thresholds()
AVIX_PANIC_LEVEL = float(_THRESHOLDS["fixed_panic_level"])
AVIX_WARNING_LEVEL = float(_THRESHOLDS["fixed_warning_level"])
AVIX_CALM_LEVEL = 20.0


def _bool(value: object) -> bool:
    try:
        return bool(value)
    except Exception:
        return False


def _finite(value: object):
    try:
        out = float(value)
    except Exception:
        return None
    if pd.isna(out):
        return None
    return round(out, 4)


def _as_trade_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d")


def prepare_s3_s4_inputs(avix_clean: pd.DataFrame, index_history: pd.DataFrame) -> pd.DataFrame:
    if avix_clean.empty or index_history.empty:
        return pd.DataFrame()
    avix = avix_clean.copy()
    avix["trade_date"] = _as_trade_date(avix["trade_date"])
    avix["avix"] = pd.to_numeric(avix.get("avix_clean"), errors="coerce")
    avix_cols = [
        "trade_date", "avix", "near_expiry", "next_expiry", "near_dte", "next_dte",
        "near_var", "next_var", "near_n_options", "next_n_options", "quality",
    ]
    avix = avix[[c for c in avix_cols if c in avix.columns]].dropna(subset=["trade_date", "avix"])

    sse = index_history[index_history["symbol"].astype(str) == "sh000001"].copy()
    if sse.empty:
        return pd.DataFrame()
    sse["trade_date"] = _as_trade_date(sse["date"])
    sse["sse_open"] = pd.to_numeric(sse.get("open"), errors="coerce")
    sse["sse_close"] = pd.to_numeric(sse.get("close"), errors="coerce")
    sse = sse[["trade_date", "sse_open", "sse_close"]].dropna(subset=["trade_date", "sse_open", "sse_close"])

    merged = avix.merge(sse, on="trade_date", how="inner")
    return merged.sort_values("trade_date").reset_index(drop=True)


def add_s3_s4_signals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().sort_values("trade_date").reset_index(drop=True)
    if out.empty:
        return out

    out["sse_ret1"] = out["sse_close"] / out["sse_close"].shift(1) - 1
    out["sse_ret10"] = out["sse_close"] / out["sse_close"].shift(10) - 1
    out["sse_ma5"] = out["sse_close"].rolling(5).mean()
    out["sse_ma10"] = out["sse_close"].rolling(10).mean()
    out["sse_prev_close"] = out["sse_close"].shift(1)
    out["sse_prev_ma5"] = out["sse_ma5"].shift(1)

    out["s3_signal"] = (
        (out["avix"] >= AVIX_PANIC_LEVEL)
        & (out["sse_ret10"] <= -0.04)
        & (out["sse_ret1"] > 0)
    )
    out["s4_signal"] = (
        (out["avix"] >= AVIX_WARNING_LEVEL)
        & (out["sse_close"] > out["sse_ma5"])
        & (out["sse_prev_close"] <= out["sse_prev_ma5"])
    )
    out["s3_s4_signal"] = out["s3_signal"] | out["s4_signal"]
    return out


def add_trade_signals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().sort_values("trade_date").reset_index(drop=True)
    for col in [
        "s3_buy", "s3_sell",
        "s4_buy", "s4_sell",
        "s3_s4_buy", "s3_s4_sell",
    ]:
        out[col] = False
    out["s3_sell_reason"] = ""
    out["s4_sell_reason"] = ""
    out["s3_s4_sell_reason"] = ""

    def run_strategy(strategy: str) -> None:
        holding = False
        buy_price = None
        buy_i = -1
        for i in range(len(out) - 1):
            row = out.iloc[i]
            if not holding:
                if _bool(row.get(f"{strategy}_signal")):
                    out.at[i, f"{strategy}_buy"] = True
                    buy_price = float(out.iloc[i + 1]["sse_open"])
                    buy_i = i + 1
                    holding = True
                continue

            current_ret = float(row["sse_close"]) / float(buy_price) - 1
            hold_days = i - buy_i + 1
            reason = ""
            if strategy == "s3":
                if float(row["avix"]) < AVIX_CALM_LEVEL:
                    reason = f"AVIX<{AVIX_CALM_LEVEL:g}"
                elif current_ret >= 0.12:
                    reason = "take_profit_12pct"
                elif current_ret <= -0.07:
                    reason = "stop_loss_7pct"
                elif hold_days >= 80:
                    reason = "holding_80_days"
            elif strategy in {"s4", "s3_s4"} and float(row["avix"]) < AVIX_CALM_LEVEL:
                reason = f"AVIX<{AVIX_CALM_LEVEL:g}"

            if reason:
                out.at[i, f"{strategy}_sell"] = True
                out.at[i, f"{strategy}_sell_reason"] = reason
                holding = False

    run_strategy("s3")
    run_strategy("s4")
    run_strategy("s3_s4")
    out["execution_trade_date"] = out["trade_date"].shift(-1)
    out["execution_sse_open"] = out["sse_open"].shift(-1)
    return out


def build_s3_s4_strategy(avix_clean: pd.DataFrame, index_history: pd.DataFrame) -> pd.DataFrame:
    prepared = prepare_s3_s4_inputs(avix_clean, index_history)
    if prepared.empty:
        return pd.DataFrame()
    out = add_trade_signals(add_s3_s4_signals(prepared))
    out["signal_quality"] = out["quality"].fillna("OK").astype(str) if "quality" in out.columns else "OK"
    return out


def latest_strategy_payload(strategy: pd.DataFrame) -> dict:
    if strategy.empty:
        return {
            "status": "empty",
            "latest": {},
            "recent_buy": [],
            "recent_sell": [],
            "rules": _rules_payload(),
        }
    df = strategy.copy().sort_values("trade_date")
    latest = df.iloc[-1]
    cols = [
        "trade_date", "execution_trade_date", "execution_sse_open", "avix",
        "sse_open", "sse_close", "sse_ret1", "sse_ret10", "sse_ma5", "sse_ma10",
        "s3_signal", "s4_signal", "s3_s4_signal",
        "s3_buy", "s3_sell", "s4_buy", "s4_sell", "s3_s4_buy", "s3_s4_sell",
        "s3_sell_reason", "s4_sell_reason", "s3_s4_sell_reason", "signal_quality",
    ]
    cols = [c for c in cols if c in df.columns]
    return {
        "status": "ready",
        "latest": _row_payload(latest, cols),
        "recent_buy": _event_rows(df, ["s3_buy", "s4_buy", "s3_s4_buy"], cols),
        "recent_sell": _event_rows(df, ["s3_sell", "s4_sell", "s3_s4_sell"], cols),
        "position": _position_payload(df),
        "rules": _rules_payload(),
    }


def _row_payload(row: pd.Series, cols: list[str]) -> dict:
    out = {}
    for col in cols:
        value = row.get(col)
        if col.endswith("_signal") or col.endswith("_buy") or col.endswith("_sell"):
            out[col] = bool(value)
        elif col.startswith("sse_") or col == "avix" or col == "execution_sse_open":
            out[col] = _finite(value)
        elif pd.isna(value):
            out[col] = None
        else:
            out[col] = value
    return out


def _event_rows(df: pd.DataFrame, flags: list[str], cols: list[str], n: int = 20) -> list[dict]:
    existing = [c for c in flags if c in df.columns]
    if not existing:
        return []
    mask = df[existing].fillna(False).astype(bool).any(axis=1)
    return [_row_payload(row, cols) for _, row in df[mask].tail(n).iterrows()]


def _position_payload(df: pd.DataFrame) -> dict:
    state = {"s3": False, "s4": False, "s3_s4": False}
    last_action = {"s3": None, "s4": None, "s3_s4": None}
    for _, row in df.iterrows():
        for name in state:
            if _bool(row.get(f"{name}_buy")):
                state[name] = True
                last_action[name] = {"action": "buy", "trade_date": row.get("trade_date")}
            if _bool(row.get(f"{name}_sell")):
                state[name] = False
                last_action[name] = {
                    "action": "sell",
                    "trade_date": row.get("trade_date"),
                    "reason": row.get(f"{name}_sell_reason") or None,
                }
    return {
        "s3": "holding" if state["s3"] else "flat",
        "s4": "holding" if state["s4"] else "flat",
        "s3_s4": "holding" if state["s3_s4"] else "flat",
        "last_action": last_action,
    }


def _rules_payload() -> dict:
    return {
        "mode": "OFFICIAL_CLOSE",
        "execution": "next_trade_day_open",
        "s3": {
            "buy": f"AVIX>={AVIX_PANIC_LEVEL:g} and SSE 10-day return<=-4% and SSE 1-day return>0",
            "sell": f"AVIX<{AVIX_CALM_LEVEL:g} or take profit 12% or stop loss 7% or holding 80 days",
        },
        "s4": {
            "buy": f"AVIX>={AVIX_WARNING_LEVEL:g} and SSE close crosses above 5-day moving average",
            "sell": f"AVIX<{AVIX_CALM_LEVEL:g}",
        },
        "s3_s4": {
            "buy": "S3 or S4",
            "sell": f"AVIX<{AVIX_CALM_LEVEL:g}",
        },
    }
