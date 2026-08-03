from __future__ import annotations

import pandas as pd

from src.core.intraday_temperature import (
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
) -> dict:
    return {
        "trade_date": trade_date,
        "update_time": update_time,
        "risk_temperature": temperature,
        "temperature_mode": mode,
        "temperature_mode_cn": "收盘正式" if final else "盘中估算",
        "is_final": final,
        "quality": "OK_TEST",
        "model_confidence": {"score": 91.25},
        "market": {
            "breadth_pressure": 54.2,
            "breadth_quality": "OK",
            "breadth_source": "TEST_STOCK_BREADTH",
        },
    }


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
    latest["market"] = {
        "breadth_pressure": None,
        "breadth_quality": "MISSING",
        "breadth_source": "",
    }
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
