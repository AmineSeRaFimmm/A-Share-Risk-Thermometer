from __future__ import annotations

from scripts import app_server


def _reset_state() -> None:
    with app_server.STATE_LOCK:
        app_server.STATE.update({
            "refresh_running": False,
            "last_refresh": None,
            "last_error": None,
            "last_mode": None,
        })


def test_realtime_fallback_rebuild_is_not_reported_as_live_success(monkeypatch) -> None:
    _reset_state()
    monkeypatch.setattr(app_server, "_sync_web_to_docs", lambda: None)
    monkeypatch.setattr(app_server, "_sync_site_data_to_docs", lambda: None)
    monkeypatch.setattr(app_server, "build_status", lambda: {"ok": True})

    def fake_run(script: str, timeout: int = 900, args=None):
        return {"script": script, "ok": script == "build_site_data.py", "seconds": 0.1}

    monkeypatch.setattr(app_server, "_run_script", fake_run)

    result = app_server.refresh_pipeline("realtime")

    assert result["ok"] is False
    assert "未获得新的实时结果" in result["error"]
    assert [step["script"] for step in result["steps"]] == [
        "update_realtime_avix.py",
        "build_site_data.py",
    ]


def test_successful_realtime_updater_is_reported_as_success(monkeypatch) -> None:
    _reset_state()
    monkeypatch.setattr(app_server, "_sync_web_to_docs", lambda: None)
    monkeypatch.setattr(app_server, "_sync_site_data_to_docs", lambda: None)
    monkeypatch.setattr(app_server, "build_status", lambda: {"ok": True})
    monkeypatch.setattr(
        app_server,
        "_run_script",
        lambda script, timeout=900, args=None: {"script": script, "ok": True, "seconds": 0.1},
    )

    result = app_server.refresh_pipeline("realtime")

    assert result["ok"] is True
    assert [step["script"] for step in result["steps"]] == ["update_realtime_avix.py"]
