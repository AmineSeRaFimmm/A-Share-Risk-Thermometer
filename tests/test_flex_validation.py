from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from research import backtest_flex_v2 as bt
from src.core import flex_validation as validation


def test_published_stats_never_inherit_old_metrics_and_omit_heavy_research(tmp_path, monkeypatch):
    from src.core import flex_engine

    monkeypatch.setattr(flex_engine, "CALCULATED", tmp_path)
    source = {"schema_version": 3, "aggressive": {"full_sample": {"performance_valid": False}},
              "scenarios": {"large": [1, 2]}, "run_manifest": {"files": {}},
              "policy_fingerprint": "current-policy"}
    (tmp_path / "flex_backtest_stats.json").write_text(json.dumps(source))
    public = flex_engine.load_backtest_stats_file()
    assert public["aggressive"] == source["aggressive"]
    assert public["policy_fingerprint"] == "current-policy"
    assert "core_only" not in public
    assert "scenarios" not in public and "run_manifest" not in public
    assert json.loads((tmp_path / "flex_backtest_stats.json").read_text()) == source


@pytest.fixture
def policy_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, "POLICY_ROOTS", ("src/core/strategy.py",))
    files = {
        "src/core/strategy.py": "from src.core.helper import value\n",
        "src/core/helper.py": "value = 1\n",
        "config/sector_etf_map.yml": "quality: good\n",
        "config/scoring.yml": "weight: 1\n",
        "requirements.txt": "pandas\n",
        "requirements-dev.txt": "pytest\n",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


@pytest.mark.parametrize("path", ["src/core/helper.py", "config/sector_etf_map.yml", "config/scoring.yml"])
def test_transitive_code_and_configuration_change_policy_hash(policy_tree, path):
    first = validation.build_policy_manifest(root=policy_tree)
    assert first["complete"]
    file = policy_tree / path
    file.write_text(file.read_text() + "# changed\n")
    second = validation.build_policy_manifest(root=policy_tree)
    assert first["policy_fingerprint"] != second["policy_fingerprint"]
    assert first["files"][path]["sha256"] != second["files"][path]["sha256"]


def test_input_append_changes_input_version_not_policy(policy_tree):
    first_policy = validation.build_policy_manifest(root=policy_tree)
    first_input = validation.build_input_versions(root=policy_tree)
    path = policy_tree / validation.INPUT_PATHS[0]
    path.parent.mkdir(parents=True)
    path.write_text("trade_date,risk_temperature\n2026-09-01,70\n")
    assert validation.build_policy_manifest(root=policy_tree) == first_policy
    assert validation.build_input_versions(root=policy_tree)["input_fingerprint"] != first_input["input_fingerprint"]


def test_missing_dependency_is_recorded(policy_tree):
    (policy_tree / "src/core/strategy.py").unlink()
    manifest = validation.build_policy_manifest(root=policy_tree)
    assert not manifest["complete"]
    assert manifest["files"]["src/core/strategy.py"]["status"] == "MISSING"


def test_actual_manifest_includes_rt_and_mapping_without_importing_rt():
    files = validation.build_policy_manifest()["files"]
    for name in (
        "research/flex_event_backtest.py", "src/core/risk_temperature.py",
        "src/core/breadth.py", "src/utils/config.py", "config/scoring.yml",
        "config/thresholds.yml", "config/sector_etf_map.yml",
    ):
        assert name in files


@pytest.mark.parametrize("dates", [[], ["2026-08-12", "2027-01-01"]])
def test_backfill_never_activates_prospective(monkeypatch, dates):
    monkeypatch.setattr(bt, "_policy_fingerprint", lambda: "new-policy")
    monkeypatch.setattr(bt, "_simulate", lambda *a, **kw: pytest.fail("prospective must not simulate backfill"))
    result = bt._prospective_validation(
        pd.DataFrame({"trade_date": pd.to_datetime(dates)}), {},
        mode="aggressive", cost=0.0001, apply_proxy_adjustment=False, event_exit=True,
    )
    assert result["status"] == "BLOCKED_REQUIRES_POINT_IN_TIME_ARCHIVE"
    assert result["sample_days"] == 0
    assert result["stats"] is None
    assert result["start"] is None
    assert not result["independent_parameter_validation"]
    assert bt.FROZEN_POLICY_FINGERPRINT is None


def result_fixture(status="COMPLETE"):
    stats = {
        "total_return": 0.1, "ann_return": 0.05, "max_dd": -0.02,
        "sharpe": 1.0, "trade_count": 2, "win_rate": 0.5,
        "net_win_rate": 0.5, "gross_win_rate": 1.0,
        "avg_trade": 0.01,
    }
    quality = {"status": status, "issues": [] if status == "COMPLETE" else [{"reason": "missing price"}]}
    return {
        "portfolio": dict(stats), "oos_portfolio": dict(stats),
        "core": dict(stats), "satellite": dict(stats),
        "turnover": {"full": 4.0, "oos": 2.0},
        "execution_quality": {"full": quality, "oos": quality},
        "walk_forward": {"aggregate": dict(stats), "folds": [{**stats, "execution_quality": quality}], "execution_quality": quality},
        "prospective": validation.blocked_prospective(policy_fingerprint="p", scenario={}),
        "params": {"tail_mode": "strict_evidence"},
    }


def test_gross_and_net_labels_and_sleeve_contributions():
    packed = bt.pack_stats(result_fixture())
    assert packed["full_sample"]["win_rate"] == packed["full_sample"]["net_win_rate"] == 0.5
    assert packed["full_sample"]["gross_win_rate"] == 1.0
    assert packed["full_sample"]["win_rate_basis"] == "net_after_costs"
    assert packed["full_sample"]["performance_valid"]
    assert not packed["core"]["standalone_strategy"]
    assert packed["core"]["ann_return"] is None
    assert "core_only" not in packed


@pytest.mark.parametrize("status", ["INCOMPLETE", "UNKNOWN"])
def test_incomplete_execution_suppresses_all_performance(status):
    packed = bt.pack_stats(result_fixture(status))
    for block in (packed["full_sample"], packed["oos"], packed["core"], packed["walk_forward"]["aggregate"], *packed["walk_forward"]["folds"]):
        for metric in ("total_return", "ann_return", "max_dd", "sharpe", "win_rate", "gross_win_rate", "net_win_rate", "avg_trade"):
            assert block[metric] is None
        assert block["trade_count"] == 2
        assert block["validation_status"] == "INCOMPLETE_EXECUTION"
        assert not block["performance_valid"]


def test_missing_quality_does_not_infer_validity():
    result = result_fixture()
    del result["execution_quality"]
    assert not bt.pack_stats(result)["full_sample"]["performance_valid"]


def test_json_has_no_nonstandard_nan():
    cleaned = validation.json_safe({"a": np.float64("nan"), "b": float("inf"), "count": np.int64(2)})
    assert json.loads(json.dumps(cleaned, allow_nan=False)) == {"a": None, "b": None, "count": 2}


@pytest.mark.parametrize("status", ["COMPLETE", "INCOMPLETE"])
def test_main_outputs_labeled_scenarios_and_no_fake_core_only(tmp_path, monkeypatch, status):
    policy = {"policy_fingerprint": "p", "complete": True}
    inputs = {"input_fingerprint": "i"}
    monkeypatch.setattr(bt, "build_policy_manifest", lambda: policy)
    monkeypatch.setattr(bt, "build_input_versions", lambda: inputs)
    monkeypatch.setattr(bt, "build_run_manifest", lambda *a, **kw: {"generated_at": "now", "run_id": "r", "sample": {"days": 2}})
    monkeypatch.setattr(bt, "load_aligned", lambda **kwargs: (pd.DataFrame({"trade_date": pd.to_datetime(["2026-09-01", "2026-09-02"])}), {}))
    monkeypatch.setattr(bt, "CALCULATED", tmp_path / "calculated")
    monkeypatch.setattr(bt, "OUT", tmp_path / "research")
    calls = []

    def simulate(*args, **kwargs):
        calls.append(kwargs)
        result = result_fixture(status)
        result["params"] = kwargs
        return result

    monkeypatch.setattr(bt, "backtest_v2", simulate)
    bt.main()
    artifact = json.loads((tmp_path / "calculated/flex_backtest_stats.json").read_text())
    assert artifact["schema_version"] == 3
    assert artifact["validation_status"] == ("GENERATED_REPLAY" if status == "COMPLETE" else "INCOMPLETE_EXECUTION")
    assert "without inspecting future window completeness" in artifact["backtest_protocol"]["right_censoring"]
    if status == "INCOMPLETE":
        assert artifact["aggressive"]["full_sample"]["net_win_rate"] is None
        assert artifact["aggressive"]["oos"]["ann_return"] is None
    assert "core_only" not in artifact
    assert artifact["core_only_status"] == "NOT_RUN_STANDALONE"
    assert len(calls) == 10
    assert artifact["aggressive"]["params"]["tail_mode"] == "strict_evidence"
    assert not artifact["aggressive"]["params"]["apply_haircut"]
    assert artifact["proxy_return_stress"]["aggressive"]["params"]["apply_haircut"]
    assert artifact["tail_assumption"]["aggressive"]["params"]["tail_mode"] == "eod_close_proxy"
    assert artifact == json.loads((tmp_path / "research/flex_v2_stats.json").read_text())


def test_asymmetric_fees_are_not_silently_repriced():
    with pytest.raises(ValueError, match="equal buy and sell costs"):
        bt.backtest_v2(pd.DataFrame(), {}, buy_cost=0.001, sell_cost=0.002, mode="aggressive")


def test_raw_backtest_result_cannot_bypass_incomplete_performance_gate(monkeypatch):
    daily = np.array([0.01, 0.02, 0.01])
    quality = {"status": "INCOMPLETE", "issues": [{"reason": "MISSING_HELD_PRICE"}]}
    result = {
        "portfolio_daily": daily, "core_daily": daily, "sat_daily": daily,
        "turnover_daily": daily, "core_trades": [], "sat_trades": [], "trades": [],
        "execution_quality": quality,
        "equity_daily": np.cumprod(1 + daily),
        "open_positions": {"core": {"qty": 1.0, "price": 100.0}},
    }
    monkeypatch.setattr(bt, "_simulate", lambda *a, **kw: result)
    monkeypatch.setattr(bt, "_fixed_policy_walk_forward", lambda *a, **kw: result_fixture("INCOMPLETE")["walk_forward"])
    monkeypatch.setattr(bt, "_policy_fingerprint", lambda: "p")
    replay = bt.backtest_v2(
        pd.DataFrame({"trade_date": pd.date_range("2024-01-01", periods=3)}), {},
        buy_cost=0.0001, sell_cost=0.0001, mode="aggressive",
    )
    for block in ("core", "satellite", "portfolio", "oos_core", "oos_portfolio"):
        assert replay[block]["total_return"] is None
        assert replay[block]["win_rate"] is None
        assert not replay[block]["performance_valid"]
    diagnostic = replay["execution_diagnostics"]["full"]
    assert diagnostic["open_positions"]
    assert diagnostic["ending_equity_diagnostic"] > 1
    assert not diagnostic["valuation_verified"]
    assert diagnostic["completed_trade_count"] == 0
