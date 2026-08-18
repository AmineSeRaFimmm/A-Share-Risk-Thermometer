from __future__ import annotations

import pandas as pd

from src.core.intraday_temperature import (
    confirmed_core_tail_dates,
    intraday_temperature_payload,
    record_intraday_temperature,
    update_intraday_temperature_history,
)


def _latest(
    update_time: str,
    temperature: float,
    *,
    trade_date: str = "2026-08-03",
    mode: str = "NOWCAST",
    final: bool = False,
    confidence: float = 91.25,
    coverage: float = 100.0,
    data_quality: float | None = None,
    missing_components: str = "",
    dd60: float = -0.07,
    breadth_mode: str = "STOCK_A",
    breadth_quality: str = "OK",
    breadth_score: float = 50.0,
    include_components: bool = True,
) -> dict:
    payload = {
        "trade_date": trade_date,
        "update_time": update_time,
        "risk_temperature": temperature,
        "temperature_mode": mode,
        "temperature_mode_cn": "收盘正式" if final else "盘中估算",
        "is_final": final,
        "quality": "OK_TEST",
        "model_confidence": {
            "score": confidence,
            "coverage_score": coverage,
            "data_quality_score": confidence if data_quality is None else data_quality,
            "missing_components": missing_components,
        },
        "market": {
            "hs300_drawdown_60d": dd60,
            "breadth_pressure": 50.0 if breadth_mode == "STOCK_A" else None,
            "breadth_mode": breadth_mode,
            "breadth_quality": breadth_quality,
            "breadth_source": "TEST_A_SPOT" if breadth_mode == "STOCK_A" else "TEST_PROXY",
        },
    }
    if include_components:
        other_score = (float(temperature) - breadth_score * 0.10) / 0.90
        payload["components"] = {
            "avix_percentile_2y": other_score,
            "avix_zscore_1y": other_score,
            "avix_5d_change": other_score,
            "qvix_confirmation": other_score,
            "realized_vol": other_score,
            "drawdown_pressure": other_score,
            "breadth_pressure": breadth_score,
            "turnover_stress": other_score,
        }
    return payload


def test_records_real_refreshes_and_replaces_same_minute() -> None:
    history, changed = update_intraday_temperature_history(
        pd.DataFrame(),
        _latest("2026-08-03T09:35:05+08:00", 63.1),
    )
    assert changed

    history, changed = update_intraday_temperature_history(
        history,
        _latest("2026-08-03T09:35:51+08:00", 63.4),
    )
    assert changed
    assert len(history) == 1
    assert history.iloc[0]["risk_temperature"] == 63.4

    history, changed = update_intraday_temperature_history(
        history,
        _latest("2026-08-03T09:45:00+08:00", 63.4),
    )
    assert changed
    assert len(history) == 2


def test_official_close_is_one_final_endpoint() -> None:
    history, _ = update_intraday_temperature_history(
        pd.DataFrame(),
        _latest("2026-08-03T15:10:00+08:00", 67.2, mode="CLOSE_PENDING"),
    )
    final = _latest(
        "2026-08-03T15:20:00+08:00",
        66.8,
        mode="OFFICIAL_CLOSE",
        final=True,
    )
    history, changed = update_intraday_temperature_history(history, final)
    assert changed
    history, changed = update_intraday_temperature_history(history, final)
    assert not changed
    assert len(history) == 2
    assert history["is_final"].sum() == 1

    payload = intraday_temperature_payload(history, "2026-08-03")
    assert payload["has_final"] is True
    assert payload["rows"][-1]["temperature_mode"] == "OFFICIAL_CLOSE"


def test_stale_rebuild_cannot_invent_intraday_sample() -> None:
    history, changed = update_intraday_temperature_history(
        pd.DataFrame(),
        _latest(
            "2026-08-04T08:00:00+08:00",
            66.8,
            trade_date="2026-08-03",
            mode="OFFICIAL_CLOSE",
            final=True,
        ),
    )
    assert not changed
    assert history.empty


def test_later_rebuild_can_correct_existing_official_endpoint() -> None:
    history, _ = update_intraday_temperature_history(
        pd.DataFrame(),
        _latest(
            "2026-08-03T15:20:00+08:00",
            57.8,
            mode="OFFICIAL_CLOSE",
            final=True,
        ),
    )
    corrected, changed = update_intraday_temperature_history(
        history,
        _latest(
            "2026-08-04T08:00:00+08:00",
            57.9,
            trade_date="2026-08-03",
            mode="OFFICIAL_CLOSE",
            final=True,
        ),
    )

    assert changed
    assert len(corrected) == 1
    assert corrected.iloc[0]["risk_temperature"] == 57.9
    assert corrected.iloc[0]["sampled_at"] == "2026-08-03T15:20:00+08:00"


def test_payload_uses_one_trade_date_and_strict_values() -> None:
    history = pd.DataFrame()
    for timestamp, temperature in [
        ("2026-08-03T09:40:00+08:00", 62.0),
        ("2026-08-03T10:00:00+08:00", 64.5),
    ]:
        history, _ = update_intraday_temperature_history(history, _latest(timestamp, temperature))
    history, _ = update_intraday_temperature_history(
        history,
        _latest(
            "2026-08-04T09:40:00+08:00",
            58.5,
            trade_date="2026-08-04",
        ),
    )

    payload = intraday_temperature_payload(history, "2026-08-03")
    assert payload["trade_date"] == "2026-08-03"
    assert payload["sample_count"] == 2
    assert [row["time"] for row in payload["rows"]] == ["09:40", "10:00"]
    assert all(0 <= row["risk_temperature"] <= 100 for row in payload["rows"])
    assert payload["eligible_count"] == 2
    assert payload["excluded_count"] == 0


def test_missing_breadth_remains_auditable_but_is_not_plot_eligible() -> None:
    latest = _latest("2026-08-03T10:26:00+08:00", 77.0)
    latest["quality"] = "OK_NOWCAST|WARN_BREADTH_MISSING"
    latest["market"].update({
        "breadth_pressure": None,
        "breadth_mode": "MISSING",
        "breadth_quality": "MISSING",
        "breadth_source": "",
    })
    history, changed = update_intraday_temperature_history(pd.DataFrame(), latest)
    assert changed
    payload = intraday_temperature_payload(history, "2026-08-03")
    assert payload["sample_count"] == 1
    assert payload["eligible_count"] == 0
    assert payload["excluded_count"] == 1
    assert payload["rows"][0]["breadth_observed"] is False
    assert payload["rows"][0]["plot_eligible"] is False


def test_legacy_rows_derive_plot_eligibility_from_quality_flag() -> None:
    history = pd.DataFrame([{
        "trade_date": "2026-08-03",
        "sampled_at": "2026-08-03T10:33:00+08:00",
        "sample_minute": "2026-08-03T10:33",
        "risk_temperature": 77.2,
        "temperature_mode": "NOWCAST",
        "temperature_mode_cn": "盘中估算",
        "is_final": False,
        "quality": "OK_NOWCAST|WARN_BREADTH_MISSING",
        "model_confidence": 71.7,
        "source_update_time": "2026-08-03T10:33:00+08:00",
    }])
    payload = intraday_temperature_payload(history, "2026-08-03")
    assert payload["eligible_count"] == 0
    assert payload["excluded_count"] == 1
    assert payload["rows"][0]["plot_eligible"] is False


def test_record_creates_empty_history_contract_for_stale_build(tmp_path) -> None:
    history_path = tmp_path / "intraday.csv"
    payload, changed = record_intraday_temperature(
        _latest(
            "2026-08-04T08:00:00+08:00",
            66.8,
            trade_date="2026-08-03",
            mode="OFFICIAL_CLOSE",
            final=True,
        ),
        history_path,
    )
    assert not changed
    assert payload["status"] == "no_samples"
    assert history_path.exists()


def test_record_backfills_delayed_official_endpoint_before_current_sample(tmp_path) -> None:
    history_path = tmp_path / "intraday.csv"
    latest = _latest(
        "2026-08-18T09:46:00+08:00",
        52.6,
        trade_date="2026-08-18",
    )
    latest["official_close"] = _latest(
        "2026-08-17T15:00:00+08:00",
        51.9,
        trade_date="2026-08-17",
        mode="OFFICIAL_CLOSE",
        final=True,
    )

    payload, changed = record_intraday_temperature(latest, history_path)
    history = pd.read_csv(history_path)

    assert changed
    assert payload["trade_date"] == "2026-08-18"
    assert payload["sample_count"] == 1
    prior_final = history[
        history["trade_date"].eq("2026-08-17") & history["is_final"]
    ]
    assert len(prior_final) == 1
    assert prior_final.iloc[0]["sampled_at"] == "2026-08-17T15:00:00+08:00"


def test_core_tail_signal_requires_three_consecutive_strict_samples() -> None:
    history = pd.DataFrame()
    for timestamp in [
        "2026-08-03T14:25:00+08:00",
        "2026-08-03T14:35:00+08:00",
    ]:
        history, _ = update_intraday_temperature_history(
            history,
            _latest(timestamp, 70.0, confidence=96.0),
        )
    signal = intraday_temperature_payload(history, "2026-08-03")["core_tail_signal"]
    assert signal["status"] == "CONFIRMING"
    assert signal["consecutive_samples"] == 2
    assert signal["stable"] is False
    assert signal["actionable"] is False


def test_core_tail_signal_prepares_then_executes_at_1450() -> None:
    history = pd.DataFrame()
    for timestamp in [
        "2026-08-03T14:25:00+08:00",
        "2026-08-03T14:35:00+08:00",
        "2026-08-03T14:45:00+08:00",
    ]:
        history, _ = update_intraday_temperature_history(
            history,
            _latest(timestamp, 70.0, confidence=96.0),
        )
    prepare = intraday_temperature_payload(history, "2026-08-03")["core_tail_signal"]
    assert prepare["status"] == "PREPARE"
    assert prepare["stable"] is True
    assert prepare["actionable"] is False

    history, _ = update_intraday_temperature_history(
        history,
        _latest("2026-08-03T14:50:00+08:00", 69.8, confidence=96.0),
    )
    execute = intraday_temperature_payload(history, "2026-08-03")["core_tail_signal"]
    assert execute["status"] == "EXECUTE"
    assert execute["consecutive_samples"] == 4
    assert execute["stability_span_minutes"] == 25
    assert execute["actionable"] is True
    assert execute["policy"]["scope"] == "CORE_ONLY"
    assert execute["valid_until"] == "2026-08-03T15:00:00+08:00"

    history, _ = update_intraday_temperature_history(
        history,
        _latest(
            "2026-08-03T15:20:00+08:00",
            69.5,
            mode="OFFICIAL_CLOSE",
            final=True,
            confidence=96.0,
        ),
    )
    final_payload = intraday_temperature_payload(history, "2026-08-03")
    assert final_payload["core_tail_signal"]["status"] == "FINAL"
    assert final_payload["core_tail_day_summary"] == {
        "ever_stable": True,
        "stable_at": "2026-08-03T14:45:00+08:00",
        "execute_triggered": True,
        "execute_at": "2026-08-03T14:50:00+08:00",
        "execute_degraded": False,
    }
    assert [row["core_tail_status"] for row in final_payload["rows"]] == [
        "CONFIRMING",
        "CONFIRMING",
        "PREPARE",
        "EXECUTE",
        "FINAL",
    ]
    assert confirmed_core_tail_dates(history) == {"2026-08-03"}


def test_valid_sample_that_fails_strategy_condition_resets_streak() -> None:
    history = pd.DataFrame()
    for timestamp in ["2026-08-03T14:20:00+08:00", "2026-08-03T14:30:00+08:00"]:
        history, _ = update_intraday_temperature_history(
            history,
            _latest(timestamp, 70.0, confidence=86.8, missing_components="QVIX_DELAYED"),
        )
    history, _ = update_intraday_temperature_history(
        history,
        _latest("2026-08-03T14:40:00+08:00", 77.0, confidence=86.8),
    )
    history, _ = update_intraday_temperature_history(
        history,
        _latest("2026-08-03T14:50:00+08:00", 70.0, confidence=86.8),
    )
    signal = intraday_temperature_payload(history, "2026-08-03")["core_tail_signal"]
    assert signal["status"] == "CONFIRMING"
    assert signal["consecutive_samples"] == 1
    assert signal["actionable"] is False


def test_degraded_pass_counts_when_full_missing_factor_range_stays_inside_rule() -> None:
    history = pd.DataFrame()
    for timestamp in [
        "2026-08-03T14:20:00+08:00",
        "2026-08-03T14:30:00+08:00",
        "2026-08-03T14:40:00+08:00",
        "2026-08-03T14:50:00+08:00",
    ]:
        history, _ = update_intraday_temperature_history(
            history,
            _latest(
                timestamp,
                70.0,
                confidence=90.0,
                coverage=90.0,
                data_quality=100.0,
                missing_components="BREADTH",
                breadth_mode="MISSING",
                breadth_quality="WARN_BREADTH_MISSING",
                breadth_score=50.0,
            ),
        )

    payload = intraday_temperature_payload(history, "2026-08-03")
    signal = payload["core_tail_signal"]
    assert signal["status"] == "EXECUTE"
    assert signal["latest_sample_state"] == "DEGRADED_PASS"
    assert signal["consecutive_samples"] == 4
    assert signal["degraded_samples_in_streak"] == 4
    assert signal["degraded"] is True
    assert signal["degraded_uncertainty"]["risk_temperature_lower"] == 65.0
    assert signal["degraded_uncertainty"]["risk_temperature_upper"] == 75.0
    assert signal["actionable"] is True
    assert signal["conditions"]["uncertainty"]["risk_temperature_lower"] == 65.0
    assert signal["conditions"]["uncertainty"]["risk_temperature_upper"] == 75.0
    assert payload["rows"][-1]["core_tail_sample_state_cn"] == "缺失数据下仍稳健通过"
    assert payload["core_tail_day_summary"]["execute_degraded"] is True


def test_degraded_bounds_survive_when_latest_sample_recovers_full_quality() -> None:
    history = pd.DataFrame()
    for timestamp in ["2026-08-03T14:30:00+08:00", "2026-08-03T14:40:00+08:00"]:
        history, _ = update_intraday_temperature_history(
            history,
            _latest(
                timestamp,
                70.0,
                confidence=90.0,
                coverage=90.0,
                data_quality=100.0,
                missing_components="BREADTH",
                breadth_mode="MISSING",
                breadth_quality="WARN_BREADTH_MISSING",
                breadth_score=50.0,
            ),
        )
    history, _ = update_intraday_temperature_history(
        history,
        _latest("2026-08-03T14:52:00+08:00", 70.0),
    )

    payload = intraday_temperature_payload(history, "2026-08-03")
    signal = payload["core_tail_signal"]
    assert signal["status"] == "EXECUTE"
    assert signal["latest_sample_state"] == "PASS"
    assert signal["conditions"]["uncertainty"] is None
    assert signal["degraded"] is True
    assert signal["degraded_samples_in_streak"] == 2
    assert signal["degraded_uncertainty"]["risk_temperature_lower"] == 65.0
    assert signal["degraded_uncertainty"]["risk_temperature_upper"] == 75.0
    assert payload["rows"][-1]["core_tail_uncertainty"]["risk_temperature_lower"] == 65.0


def test_degraded_fail_resets_streak_when_drawdown_definitively_fails() -> None:
    history = pd.DataFrame()
    for timestamp in ["2026-08-03T14:20:00+08:00", "2026-08-03T14:30:00+08:00"]:
        history, _ = update_intraday_temperature_history(history, _latest(timestamp, 70.0))
    history, _ = update_intraday_temperature_history(
        history,
        _latest(
            "2026-08-03T14:40:00+08:00",
            70.0,
            confidence=90.0,
            coverage=90.0,
            data_quality=100.0,
            missing_components="BREADTH",
            dd60=-0.04,
            breadth_mode="MISSING",
            breadth_quality="WARN_BREADTH_MISSING",
            breadth_score=50.0,
        ),
    )
    history, _ = update_intraday_temperature_history(
        history,
        _latest("2026-08-03T14:50:00+08:00", 70.0),
    )

    payload = intraday_temperature_payload(history, "2026-08-03")
    assert payload["rows"][2]["core_tail_sample_state"] == "DEGRADED_FAIL"
    assert payload["core_tail_signal"]["status"] == "CONFIRMING"
    assert payload["core_tail_signal"]["consecutive_samples"] == 1


def test_indeterminate_sample_is_skipped_without_resetting_valid_streak() -> None:
    history = pd.DataFrame()
    samples = [
        ("2026-08-03T14:20:00+08:00", {}),
        (
            "2026-08-03T14:30:00+08:00",
            {
                "coverage": 90.0,
                "data_quality": 100.0,
                "missing_components": "BREADTH",
                "breadth_mode": "MISSING",
                "breadth_quality": "WARN_BREADTH_MISSING",
                "breadth_score": 90.0,
            },
        ),
        ("2026-08-03T14:40:00+08:00", {}),
        ("2026-08-03T14:50:00+08:00", {}),
    ]
    for timestamp, kwargs in samples:
        history, _ = update_intraday_temperature_history(
            history,
            _latest(timestamp, 70.0, confidence=86.8, **kwargs),
        )

    signal = intraday_temperature_payload(history, "2026-08-03")["core_tail_signal"]
    assert signal["status"] == "EXECUTE"
    assert signal["consecutive_samples"] == 3
    assert signal["invalid_samples_skipped"] == 1
    assert signal["actionable"] is True


def test_unbounded_missing_sample_remains_invalid_and_is_skipped() -> None:
    history = pd.DataFrame()
    for timestamp in [
        "2026-08-03T14:20:00+08:00",
        "2026-08-03T14:30:00+08:00",
        "2026-08-03T14:40:00+08:00",
    ]:
        history, _ = update_intraday_temperature_history(history, _latest(timestamp, 70.0))
    history, _ = update_intraday_temperature_history(
        history,
        _latest(
            "2026-08-03T14:50:00+08:00",
            70.0,
            confidence=90.0,
            coverage=90.0,
            data_quality=100.0,
            missing_components="BREADTH",
            breadth_mode="MISSING",
            breadth_quality="WARN_BREADTH_MISSING",
            include_components=False,
        ),
    )

    signal = intraday_temperature_payload(history, "2026-08-03")["core_tail_signal"]
    assert signal["status"] == "DATA_WAIT"
    assert signal["latest_sample_state"] == "INVALID"
    assert signal["consecutive_samples"] == 3
    assert signal["actionable"] is False


def test_latest_indeterminate_sample_pauses_execution_but_preserves_streak() -> None:
    history = pd.DataFrame()
    for timestamp in [
        "2026-08-03T14:20:00+08:00",
        "2026-08-03T14:30:00+08:00",
        "2026-08-03T14:40:00+08:00",
    ]:
        history, _ = update_intraday_temperature_history(
            history,
            _latest(timestamp, 70.0, confidence=86.8),
        )
    history, _ = update_intraday_temperature_history(
        history,
        _latest(
            "2026-08-03T14:50:00+08:00",
            70.0,
            confidence=90.0,
            coverage=90.0,
            data_quality=100.0,
            missing_components="BREADTH",
            breadth_mode="MISSING",
            breadth_quality="WARN_BREADTH_MISSING",
            breadth_score=90.0,
        ),
    )

    signal = intraday_temperature_payload(history, "2026-08-03")["core_tail_signal"]
    assert signal["status"] == "DATA_WAIT"
    assert signal["latest_sample_state"] == "INDETERMINATE"
    assert "RT可能区间 61.0-71.0" in signal["status_cn"]
    assert signal["consecutive_samples"] == 3
    assert signal["stable"] is True
    assert signal["actionable"] is False

    payload = intraday_temperature_payload(history, "2026-08-03")
    assert payload["rows"][-1]["core_tail_status"] == "DATA_WAIT"
    assert payload["rows"][-1]["core_tail_sample_state"] == "INDETERMINATE"
    assert payload["core_tail_day_summary"]["execute_triggered"] is False


def test_skipped_samples_cannot_bridge_more_than_twenty_minutes() -> None:
    history = pd.DataFrame()
    history, _ = update_intraday_temperature_history(
        history,
        _latest("2026-08-03T14:20:00+08:00", 70.0, confidence=86.8),
    )
    history, _ = update_intraday_temperature_history(
        history,
        _latest(
            "2026-08-03T14:35:00+08:00",
            70.0,
            confidence=90.0,
            coverage=90.0,
            data_quality=100.0,
            missing_components="BREADTH",
            breadth_mode="MISSING",
            breadth_quality="WARN_BREADTH_MISSING",
            breadth_score=90.0,
        ),
    )
    for timestamp in ["2026-08-03T14:45:00+08:00", "2026-08-03T14:50:00+08:00"]:
        history, _ = update_intraday_temperature_history(
            history,
            _latest(timestamp, 70.0, confidence=86.8),
        )

    signal = intraday_temperature_payload(history, "2026-08-03")["core_tail_signal"]
    assert signal["status"] == "CONFIRMING"
    assert signal["consecutive_samples"] == 2
    assert signal["actionable"] is False
