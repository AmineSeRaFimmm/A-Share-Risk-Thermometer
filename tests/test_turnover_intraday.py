from __future__ import annotations

import pandas as pd

from src.core.risk_temperature import _session_progress, compute_turnover


def test_session_progress_excludes_lunch_break():
    assert _session_progress("2026-07-30T10:30:00+08:00", "2026-07-30") == 0.25
    assert _session_progress("2026-07-30T12:00:00+08:00", "2026-07-30") == 0.5
    assert _session_progress("2026-07-30T14:00:00+08:00", "2026-07-30") == 0.75
    assert _session_progress("2026-07-30T15:00:00+08:00", "2026-07-30") == 1.0


def test_intraday_turnover_compares_full_day_equivalent_volume():
    history = pd.DataFrame([
        {
            "date": f"2026-07-{day:02d}",
            "symbol": "sh000300",
            "volume": 1000.0,
            "source": "EOD",
        }
        for day in range(1, 11)
    ] + [{
        "date": "2026-07-30",
        "symbol": "sh000300",
        "volume": 250.0,
        "source": "TENCENT_INDEX_QUOTE_RT",
        "quote_time": "2026-07-30T10:30:00+08:00",
    }])

    row = compute_turnover(history).iloc[-1]
    assert float(row["session_progress"]) == 0.25
    assert float(row["full_day_volume_estimate"]) == 1000.0
    assert float(row["volume_mean_20_baseline"]) == 1000.0
    assert float(row["volume_ratio_20"]) == 1.0
