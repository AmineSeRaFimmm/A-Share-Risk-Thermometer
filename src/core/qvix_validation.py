from __future__ import annotations
import numpy as np
import pandas as pd
from src.utils.config import load_thresholds

REPLICA_WINDOW = 252
REPLICA_MIN_OBS = 20
REPLICA_LOW_OBS = 5
_THRESHOLDS = load_thresholds()
MIN_QVIX_CORR_60 = float(_THRESHOLDS["min_qvix_corr_60"])
PERCENTILE_WARNING = float(_THRESHOLDS["percentile_warning"])


def _add_qvix_replica(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["avix_clean"] = pd.to_numeric(out["avix_clean"], errors="coerce")
    out["qvix_close"] = pd.to_numeric(out["qvix_close"], errors="coerce")
    valid_pair = out["avix_clean"].notna() & out["qvix_close"].notna() & out["qvix_close"].gt(0)
    prior_spread = (out["avix_clean"] - out["qvix_close"]).where(valid_pair).shift(1)
    basis = prior_spread.rolling(REPLICA_WINDOW, min_periods=REPLICA_LOW_OBS).median()
    obs_count = prior_spread.rolling(REPLICA_WINDOW, min_periods=1).count().fillna(0).astype(int)
    out["qvix_replica_basis"] = basis.fillna(0.0)
    out["qvix_replica_calibration_count"] = obs_count
    out["qvix_replica"] = (out["avix_clean"] - out["qvix_replica_basis"]).where(out["avix_clean"].notna())
    out.loc[out["qvix_replica"] <= 0, "qvix_replica"] = np.nan
    out["qvix_replica_quality"] = np.select(
        [
            obs_count >= REPLICA_MIN_OBS,
            obs_count >= REPLICA_LOW_OBS,
        ],
        [
            "OK_REPLICA_ROLLING_MEDIAN_BASIS",
            "WARN_REPLICA_LOW_CALIBRATION",
        ],
        default="WARN_REPLICA_UNCALIBRATED",
    )
    out["qvix_replica_method"] = "AVIX_CLEAN_MINUS_PRIOR_252D_MEDIAN_AVIX_QVIX_SPREAD"
    return out


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
        av = _add_qvix_replica(av)
        return av[[
            "trade_date", "avix_clean", "qvix_close", "qvix_replica", "qvix_replica_basis",
            "qvix_replica_calibration_count", "qvix_replica_quality", "qvix_replica_method",
            "avix_change_1d", "qvix_change_1d",
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
    df["extreme_match"] = (av_pct >= PERCENTILE_WARNING) == (q_pct >= PERCENTILE_WARNING)
    def score(row):
        if pd.isna(row.qvix_close):
            return 50.0
        if bool(row.direction_match) and (pd.isna(row.rolling_corr_60) or row.rolling_corr_60 >= MIN_QVIX_CORR_60) and (pd.isna(row.spread_zscore_252) or abs(row.spread_zscore_252) <= 2):
            return 100.0
        if pd.notna(row.rolling_corr_60) and row.rolling_corr_60 >= MIN_QVIX_CORR_60:
            return 60.0
        return 30.0
    df["qvix_confirmation"] = df.apply(score, axis=1)
    df["quality"] = df["qvix_close"].isna().map(lambda missing: "WARN_QVIX_MISSING" if missing else "OK")
    df = _add_qvix_replica(df)
    return df[[
        "trade_date", "avix_clean", "qvix_close", "qvix_replica", "qvix_replica_basis",
        "qvix_replica_calibration_count", "qvix_replica_quality", "qvix_replica_method",
        "avix_change_1d", "qvix_change_1d",
        "direction_match", "spread", "spread_zscore_252", "rolling_corr_60",
        "rolling_corr_120", "extreme_match", "qvix_confirmation", "quality",
    ]]
