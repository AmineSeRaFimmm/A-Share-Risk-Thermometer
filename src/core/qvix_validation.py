from __future__ import annotations
import numpy as np
import pandas as pd

def validate_qvix(avix_clean: pd.DataFrame, qvix: pd.DataFrame) -> pd.DataFrame:
    if avix_clean.empty:
        return pd.DataFrame()
    av = avix_clean[["trade_date", "avix_clean"]].copy()
    if qvix.empty:
        av["qvix_close"] = np.nan
        av["avix_change_1d"] = av["avix_clean"].diff()
        av["qvix_change_1d"] = np.nan
        av["direction_match"] = False
        av["spread"] = np.nan
        av["spread_zscore_252"] = np.nan
        av["rolling_corr_60"] = np.nan
        av["rolling_corr_120"] = np.nan
        av["extreme_match"] = False
        av["qvix_confirmation"] = 50.0
        av["quality"] = "WARN_QVIX_MISSING"
        return av[[
            "trade_date", "avix_clean", "qvix_close", "avix_change_1d", "qvix_change_1d",
            "direction_match", "spread", "spread_zscore_252", "rolling_corr_60",
            "rolling_corr_120", "extreme_match", "qvix_confirmation", "quality",
        ]]
    q = qvix.rename(columns={"date": "trade_date", "close": "qvix_close"})[["trade_date", "qvix_close"]].copy()
    q["qvix_close"] = pd.to_numeric(q["qvix_close"], errors="coerce")
    df = av.merge(q, on="trade_date", how="left").sort_values("trade_date")
    df["avix_change_1d"] = df["avix_clean"].diff()
    df["qvix_change_1d"] = df["qvix_close"].diff()
    df["direction_match"] = np.sign(df["avix_change_1d"]) == np.sign(df["qvix_change_1d"])
    df["spread"] = df["avix_clean"] - df["qvix_close"]
    df["spread_zscore_252"] = (df["spread"] - df["spread"].rolling(252, min_periods=20).mean()) / df["spread"].rolling(252, min_periods=20).std()
    ret = df[["avix_change_1d", "qvix_change_1d"]]
    df["rolling_corr_60"] = ret["avix_change_1d"].rolling(60, min_periods=20).corr(ret["qvix_change_1d"])
    df["rolling_corr_120"] = ret["avix_change_1d"].rolling(120, min_periods=40).corr(ret["qvix_change_1d"])
    av_pct = df["avix_clean"].rolling(504, min_periods=20).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    q_pct = df["qvix_close"].rolling(504, min_periods=20).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    df["extreme_match"] = (av_pct >= 0.80) == (q_pct >= 0.80)
    def score(row):
        if pd.isna(row.qvix_close):
            return 50.0
        if bool(row.direction_match) and (pd.isna(row.rolling_corr_60) or row.rolling_corr_60 >= 0.60) and (pd.isna(row.spread_zscore_252) or abs(row.spread_zscore_252) <= 2):
            return 100.0
        if pd.notna(row.rolling_corr_60) and row.rolling_corr_60 >= 0.60:
            return 60.0
        return 30.0
    df["qvix_confirmation"] = df.apply(score, axis=1)
    df["quality"] = df["qvix_close"].isna().map(lambda missing: "WARN_QVIX_MISSING" if missing else "OK")
    return df[[
        "trade_date", "avix_clean", "qvix_close", "avix_change_1d", "qvix_change_1d",
        "direction_match", "spread", "spread_zscore_252", "rolling_corr_60",
        "rolling_corr_120", "extreme_match", "qvix_confirmation", "quality",
    ]]
