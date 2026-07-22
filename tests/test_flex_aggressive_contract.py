from __future__ import annotations

import numpy as np

from research.backtest_flex_v2 import instrument_path_returns
from src.core.flex_engine import merge_satellite_targets, quality_adjusted_return


def test_proxy_adjustment_does_not_shrink_losses():
    assert quality_adjusted_return(0.10, "proxy") == 0.085
    assert quality_adjusted_return(-0.10, "proxy") < -0.10


def test_multi_stage_sector_keeps_stage_evidence():
    longs, _avoids, _suppressed = merge_satellite_targets(["CSI300_CORE_BUY", "HIGH_COOLING"])
    media = next(x for x in longs if x["name"] == "传媒")
    assert set(media["stages"]) == {"CSI300_CORE_BUY", "HIGH_COOLING"}
    assert {x["stage_id"] for x in media["stage_evidence"]} == {"CSI300_CORE_BUY", "HIGH_COOLING"}


def test_backtest_uses_real_daily_path_not_endpoint_smoothing():
    opens = np.array([10.0, 10.0, 20.0, 11.0])
    closes = np.array([10.0, 20.0, 11.0, 11.0])
    path = instrument_path_returns(opens, closes, 1, 3)
    assert set(path) == {1, 2, 3}
    assert np.isclose(path[1], 1.0)
    assert np.isclose(path[2], -0.45)
    assert np.isclose(path[3], 0.0)
