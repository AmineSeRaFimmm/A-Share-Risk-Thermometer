from __future__ import annotations

import numpy as np
import pandas as pd

from research.backtest_flex_v2 import (
    FROZEN_POLICY_FINGERPRINT,
    _apply_sat_risk_exit,
    _path_total,
    _policy_fingerprint,
    _simulate,
    instrument_path_returns,
)
from src.core.flex_engine import merge_satellite_targets, quality_adjusted_return
from src.core.sector_etf_map import attach_etf_fields
from src.core.stage_trade_playbook import build_playbook_payload


def test_proxy_adjustment_does_not_shrink_losses():
    assert quality_adjusted_return(0.10, "proxy") == 0.085
    assert quality_adjusted_return(-0.10, "proxy") < -0.10


def test_multi_stage_sector_keeps_stage_evidence():
    longs, _avoids, _suppressed = merge_satellite_targets(["CSI300_CORE_BUY", "HIGH_COOLING"])
    media = next(x for x in longs if x["name"] == "传媒")
    assert set(media["stages"]) == {"CSI300_CORE_BUY", "HIGH_COOLING"}
    assert {x["stage_id"] for x in media["stage_evidence"]} == {"CSI300_CORE_BUY", "HIGH_COOLING"}


def test_stage_specific_evidence_drives_score_and_direction_conflicts():
    longs, avoids, _suppressed = merge_satellite_targets(["FALLING_HARD", "HIGH_COOLING"])
    coal = next(x for x in longs if x["name"] == "煤炭")
    assert {(x["stage_id"], x["n"]) for x in coal["stage_evidence"]} == {
        ("FALLING_HARD", 339),
        ("HIGH_COOLING", 37),
    }
    assert "传媒" not in {x["name"] for x in longs}
    media_avoid = next(x for x in avoids if x["name"] == "传媒")
    assert media_avoid["n"] == 477
    assert media_avoid["conflict_resolution"]


def test_shared_etf_alias_never_emits_long_and_avoid_together():
    longs, avoids, _suppressed = merge_satellite_targets(["CSI300_CORE_BUY", "RISING_HARD"])
    long_codes = {x["etf_code"] for x in map(attach_etf_fields, longs)}
    avoid_codes = {x["etf_code"] for x in map(attach_etf_fields, avoids)}
    assert long_codes.isdisjoint(avoid_codes)


def test_satellite_eod_risk_exit_realizes_next_open_gap():
    close_path = {0: 0.02, 1: 0.02, 2: 0.01, 3: 0.00, 4: 0.00}
    next_open = {1: 0.00, 2: 0.00, 3: -0.05, 4: 0.00}

    realized, exit_i = _apply_sat_risk_exit(close_path, next_open, 0, 4)

    assert exit_i == 3
    assert realized[3] == -0.05
    assert _path_total(realized) < 0.0


def test_prospective_policy_fingerprint_is_frozen():
    assert _policy_fingerprint() == FROZEN_POLICY_FINGERPRINT


def test_sample_tail_does_not_count_incomplete_core_trade():
    dates = pd.bdate_range("2026-08-03", periods=6)
    frame = pd.DataFrame(
        {
            "trade_date": dates,
            "rt": [70.0] + [50.0] * 5,
            "dd60": [-0.06] + [0.0] * 5,
            "csi_open": [100.0] * 6,
            "csi_close": [101.0] * 6,
            "next_open": [100.0] * 6,
            "rt_d5": [np.nan] * 6,
            "rt_d1": [np.nan] * 6,
            "rt_rollmax_10": [70.0] * 6,
            "prev_rt": [np.nan] * 6,
        }
    )
    result = _simulate(
        frame,
        {"sector_open": {}, "sector_close": {}, "names": []},
        mode="aggressive",
        cost=0.0001,
        apply_proxy_adjustment=True,
        event_exit=True,
        start_i=0,
    )
    assert result["core_trades"] == []


def test_backtest_uses_real_daily_path_not_endpoint_smoothing():
    opens = np.array([10.0, 10.0, 20.0, 11.0])
    closes = np.array([10.0, 20.0, 11.0, 11.0])
    path = instrument_path_returns(opens, closes, 1, 3)
    assert set(path) == {1, 2, 3}
    assert np.isclose(path[1], 1.0)
    assert np.isclose(path[2], -0.45)
    assert np.isclose(path[3], 0.0)


def test_published_actionable_instructions_are_engine_resolved(monkeypatch):
    monkeypatch.setattr("src.core.flex_engine.save_position_state", lambda _state: None)
    risk = pd.DataFrame(
        [
            {
                "trade_date": "2026-08-10",
                "risk_temperature": 70.0,
                "sh000300_dd60": -0.06,
                "regime": "CAUTION",
                "regime_cn": "警戒",
                "quality": "OK",
                "model_confidence": 93.7,
            }
        ]
    )
    payload = build_playbook_payload(risk, pd.DataFrame())
    assert payload["data_quality"]["official_as_of"] == "2026-08-10"
    actions = payload["actionable_instructions"]
    assert actions == payload["flex_panel"]["all_actions"]
    long_ids = {
        item.get("etf_code") or item.get("name")
        for item in actions
        if item.get("action") in {"OPEN", "HOLD"}
    }
    avoid_ids = {
        item.get("etf_code") or item.get("name")
        for item in actions
        if item.get("action") == "AVOID"
    }
    assert long_ids.isdisjoint(avoid_ids)
    assert payload["stage_observations"]
