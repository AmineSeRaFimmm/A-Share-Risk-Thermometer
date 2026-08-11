from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.backtest_flex_dense_grid import (
    active_days,
    combine_port,
    simulate_core_daily,
    simulate_sat_daily,
    stats_from_daily,
)


def test_dense_core_uses_observed_daily_path():
    rt = np.array([70.0, 50.0, 50.0, 50.0, 50.0])
    dd = np.array([-0.06, 0.0, 0.0, 0.0, 0.0])
    opens = np.array([100.0, 100.0, 110.0, 110.0, 110.0])
    closes = np.array([100.0, 200.0, 110.0, 110.0, 110.0])

    daily, trades = simulate_core_daily(
        rt,
        dd,
        opens,
        closes,
        rt_low=60.0,
        rt_high=80.0,
        dd_max=-0.05,
        hold_days=2,
        buy_cost=0.0,
        sell_cost=0.0,
    )

    assert daily[1] == 1.0
    assert daily[2] == pytest.approx(-0.45)
    assert daily[3] == 0.0
    assert trades == pytest.approx([0.1])


def test_dense_satellite_uses_observed_basket_path():
    n = 10
    frame = pd.DataFrame({"trade_date": pd.bdate_range("2026-07-01", periods=n)})
    stages = [["ENTER_70_BOUNCE"]] + [[] for _ in range(n - 1)]
    opens = np.full(n, 100.0)
    closes = np.full(n, 100.0)
    closes[1] = 110.0
    closes[2] = 99.0
    closes[3:6] = 102.0
    daily, trades = simulate_sat_daily(
        frame,
        {"sector_open": {"恒生科技": opens}, "sector_close": {"恒生科技": closes}},
        stages,
        sat_min=3,
        sat_default=5,
        sat_max=8,
        buy_cost=0.0,
        sell_cost=0.0,
        apply_haircut=False,
        event_exit=False,
    )

    assert daily[1] == pytest.approx(0.1)
    assert daily[2] == pytest.approx(-0.1)
    assert len(set(np.round(daily[1:6], 8))) > 1
    assert np.isclose(trades[0], 0.0)


def test_flat_holding_day_remains_invested_for_weighting_and_exposure():
    rt = np.array([70.0, 50.0, 50.0, 50.0, 50.0])
    dd = np.array([-0.06, 0.0, 0.0, 0.0, 0.0])
    prices = np.full(5, 100.0)
    core, _ = simulate_core_daily(
        rt,
        dd,
        prices,
        prices,
        rt_low=60.0,
        rt_high=80.0,
        dd_max=-0.05,
        hold_days=2,
        buy_cost=0.0,
        sell_cost=0.0,
    )
    satellite = np.zeros(5)
    satellite[2] = 0.1

    combined = combine_port(
        core,
        satellite,
        w_core=0.6,
        w_sat=0.4,
        total_cap=1.0,
        flex_single_full=True,
    )

    assert np.array_equal(active_days(core), [False, True, True, True, False])
    assert combined[2] == pytest.approx(0.04)
    assert stats_from_daily(core)["exposure_ratio"] == pytest.approx(3 / 5)
