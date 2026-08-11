from __future__ import annotations

import pandas as pd

from scripts import bootstrap_history
from src.storage.csv_store import read_csv, write_csv


def _index_rows(start: str, periods: int, symbol: str) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=periods).strftime("%Y-%m-%d")
    values = pd.Series(range(periods), dtype=float) + 4000.0
    return pd.DataFrame({
        "date": dates,
        "open": values,
        "close": values,
        "high": values,
        "low": values,
        "volume": 1.0,
        "symbol": symbol,
        "source": "TEST",
        "fetch_time": "2026-08-11T15:00:00+08:00",
    })


def test_recent_rebuild_preserves_full_index_cache(monkeypatch, tmp_path) -> None:
    raw = tmp_path / "raw"
    normalized = tmp_path / "normalized"
    (raw / "indices").mkdir(parents=True)
    normalized.mkdir()
    symbol = "sh000300"
    old = _index_rows("2020-01-02", 300, symbol)
    fresh = old.tail(20).copy()
    fresh["close"] = fresh["close"] + 10.0
    write_csv(old, raw / "indices" / f"{symbol}.csv")

    monkeypatch.setattr(bootstrap_history, "RAW", raw)
    monkeypatch.setattr(bootstrap_history, "NORMALIZED", normalized)
    monkeypatch.setattr(
        bootstrap_history,
        "load_yaml",
        lambda _name: {"indices": {"hs300": {"symbol": symbol}}},
    )
    monkeypatch.setattr(bootstrap_history, "fetch_index_daily", lambda _symbol: fresh)

    result = bootstrap_history.fetch_indices(recent_days=120)
    cached = read_csv(raw / "indices" / f"{symbol}.csv")

    assert len(result) == 300
    assert len(cached) == 300
    assert result["date"].min() == old["date"].min()
    assert float(result.iloc[-1]["close"]) == float(fresh.iloc[-1]["close"])
