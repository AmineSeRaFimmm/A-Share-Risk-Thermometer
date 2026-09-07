import numpy as np
import pandas as pd

from research import backtest_core_plus_sectors as source


def test_strict_loader_preserves_missing_sessions_and_prices(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "ROOT", tmp_path)
    dates = pd.bdate_range("2026-08-03", periods=3)
    frames = {
        "data/calculated/risk_components.csv": pd.DataFrame({
            "trade_date": dates, "risk_temperature": [50, 51, 52]}),
        "data/raw/indices/sh000300.csv": pd.DataFrame({
            "date": dates[[0, 2]], "open": [100, 102], "close": [100, 102],
            "high": [101, 103], "low": [99, 101]}),
        "data/normalized/sw_level1_sector_history.csv": pd.DataFrame({
            "date": dates, "name": "A", "open": np.nan, "close": [100, 101, 102]}),
        "data/raw/indices/hstech.csv": pd.DataFrame({
            "date": dates[[0, 2]], "open": [100, 102], "close": [100, 102]}),
    }
    for name, frame in frames.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
    strict, meta = source.load_aligned(allow_price_imputation=False)
    assert len(strict) == 3
    assert np.isnan(strict.loc[1, "csi_open"])
    assert np.isnan(meta["sector_open"]["A"]).all()
    assert np.isnan(meta["sector_open"]["恒生科技"][1])
    assert np.isnan(meta["sector_close"]["恒生科技"][1])
    legacy, old_meta = source.load_aligned()
    assert len(legacy) == 2
    assert np.isfinite(old_meta["sector_open"]["A"]).all()
