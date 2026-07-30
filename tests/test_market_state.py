from __future__ import annotations

from src.core.market_state import CLOSE_PENDING, ESTIMATED_CLOSE, INTRADAY, estimated_temperature_mode


def test_temperature_mode_transitions_at_close():
    assert estimated_temperature_mode("2026-07-30T14:59:59+08:00")[0] == INTRADAY
    assert estimated_temperature_mode("2026-07-30T15:00:00+08:00")[0] == CLOSE_PENDING
    assert estimated_temperature_mode("2026-07-30T15:15:59+08:00")[0] == CLOSE_PENDING
    assert estimated_temperature_mode("2026-07-30T15:16:00+08:00")[0] == ESTIMATED_CLOSE
