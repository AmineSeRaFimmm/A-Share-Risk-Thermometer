from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.flex_event_backtest import simulate_execution
from research.backtest_flex_v2 import instrument_path_returns, sleeve_stats
from src.core.flex_execution import fixed_basket_return, rebalance_values, satellite_exit_reason


def frame(n=12):
    return pd.DataFrame({
        "trade_date": pd.bdate_range("2026-08-03", periods=n),
        "rt": 50.0, "dd60": 0.0, "prev_rt": 50.0, "rt_d1": 0.0,
        "rt_d5": 5.0, "rt_rollmax_10": 50.0,
        "csi_open": 100.0, "csi_close": 100.0,
    })


def replay(f, meta=None, **kwargs):
    return simulate_execution(
        f, meta or {"sector_open": {}, "sector_close": {}},
        mode="aggressive", cost=kwargs.pop("cost", 0.0),
        apply_proxy_adjustment=False, event_exit=True, start_i=0, **kwargs,
    )


def basket(monkeypatch, names=("A", "B")):
    monkeypatch.setattr("research.flex_event_backtest.merge_satellite_targets", lambda *_a, **_k: (
        [{"name": name, "weight_in_sat": 1 / len(names)} for name in names], [], []))


def test_fixed_entry_basket_does_not_rebalance_itself():
    ret = fixed_basket_return({"A": .5, "B": .5}, {"A": 100, "B": 100}, {"A": 103.059, "B": 103.059}, entry_cost_rate=0)
    assert ret == pytest.approx(.03059)
    assert satellite_exit_reason(3, ret) is None
    assert fixed_basket_return({"A": 1}, {"A": 100}, {"A": np.nan}) is None


def test_account_and_risk_use_the_same_fixed_basket(monkeypatch):
    basket(monkeypatch)
    f = frame()
    a = np.array([100, 110, 99] + [103.059] * 9, dtype=float)
    b = np.array([100, 90, 99] + [103.059] * 9, dtype=float)
    meta = {"sector_open": {"A": np.r_[100, 100, a[1:-1]], "B": np.r_[100, 100, b[1:-1]]}, "sector_close": {"A": a, "B": b}}
    result = replay(f, meta)
    assert result["equity_daily"][3] == pytest.approx(1.03059)
    assert not [e for e in result["events"] if e["date"] == "2026-08-07" and e["reason"] == "TAKE_PROFIT"]
    assert result["sat_trades"][0].exit_reason == "MAX_HOLD"


def test_tail_allocation_does_not_reweight_earlier_day_profit(monkeypatch):
    basket(monkeypatch, ("A",))
    f = frame(8)
    close = np.array([100, 100, 100, 105, 105, 105, 105, 105.])
    evidence = {
        "eligible": True, "point_in_time_verified": True,
        "risk_temperature": 70, "hs300_drawdown_60d": -.07,
        "decision_at": "2026-08-06T14:50:00+08:00", "fill_at": "2026-08-06T14:51:00+08:00",
        "sample_count": 3, "stable_minutes": 15, "prices": {"core": 100, "A": 105},
    }
    meta = {"sector_open": {"A": np.r_[100, close[:-1]]}, "sector_close": {"A": close}, "tail_evidence": {"2026-08-06": evidence}}
    result = replay(f, meta)
    assert result["portfolio_daily"][3] == pytest.approx(.05)
    assert result["execution_quality"]["verified_tail_entries"] == 1
    assert len([e for e in result["events"] if e["phase"] == "tail" and e["name"] == "core"]) == 1


def test_eod_alpha_alone_never_proves_tail_entry():
    f = frame(8)
    f.loc[0, ["rt", "dd60"]] = [70, -.07]
    result = replay(f, enabled_sleeves=("core",))
    assert result["events"][0]["date"] == "2026-08-04"
    assert result["events"][0]["phase"] == "open"


def test_net_trade_win_rate_includes_actual_assigned_costs():
    f = frame(8)
    f.loc[0, ["rt", "dd60"]] = [70, -.06]
    f.loc[6:, ["csi_open", "csi_close"]] = 100.01
    result = replay(f, enabled_sleeves=("core",), cost=.0001)
    t = result["core_trades"][0]
    assert t.gross_ret > 0 and t.ret < 0
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.fees)
    assert result["fees_total"] == pytest.approx(sum(e["fee"] for e in result["events"]))
    stats = sleeve_stats(result["portfolio_daily"], result["trades"], "test")
    assert stats["gross_win_rate"] == 1 and stats["net_win_rate"] == 0


def test_missing_execution_price_blocks_instead_of_inventing_flat_return():
    f = frame(8)
    f.loc[0, ["rt", "dd60"]] = [70, -.06]
    f.loc[1, "csi_open"] = np.nan
    result = replay(f, enabled_sleeves=("core",))
    assert result["execution_quality"]["status"] == "INCOMPLETE"
    assert result["events"] == []
    assert instrument_path_returns(np.array([100., 100., np.nan]), np.array([100., 101., 102.]), 0, 2) is None


def test_open_tail_position_is_marked_but_not_a_completed_trade():
    f = frame(4)
    f.loc[0, ["rt", "dd60"]] = [70, -.06]
    f.loc[1:, "csi_close"] = 105
    f.loc[2:, "csi_open"] = 105
    result = replay(f, enabled_sleeves=("core",))
    assert result["trades"] == []
    assert result["open_positions"]
    assert result["equity_daily"][-1] == pytest.approx(1.05)


def test_rebalance_uses_drifted_values_and_deducts_fees_before_sizing():
    current = {"core": .66, "sat": .4}
    target, fee = rebalance_values(1.06, current, {"sat": 1.0}, .0001)
    assert fee == pytest.approx((.66 + abs(target["sat"] - .4)) * .0001)
    assert target["sat"] + fee == pytest.approx(1.06)
