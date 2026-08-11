from __future__ import annotations

from pathlib import Path

from scripts.stage_realtime_outputs import ALLOWED_PATHS


ROOT = Path(__file__).resolve().parents[1]


def test_temperature_page_and_published_assets_are_in_sync() -> None:
    for relative in ["index.html", "assets/app.js", "assets/charts.js", "assets/app.css"]:
        assert (ROOT / "web" / relative).read_text(encoding="utf-8") == (
            ROOT / "docs" / relative
        ).read_text(encoding="utf-8")


def test_temperature_page_loads_persisted_intraday_series() -> None:
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    app = (ROOT / "web/assets/app.js").read_text(encoding="utf-8")
    charts = (ROOT / "web/assets/charts.js").read_text(encoding="utf-8")

    assert 'id="intradayTemperatureChart"' in html
    assert 'id="intradayTemperatureNote"' in html
    assert "./data/intraday_temperature.json" in app
    assert "function renderIntradayTemperaturePanel(payload)" in app
    assert "function renderIntradayTemperatureChart(payload)" in charts
    assert "今日尚无刷新采样" in charts
    assert "正式收盘终点" in charts
    assert "type: 'time'" in charts
    assert "T08:45:00+08:00" in charts
    assert "T15:30:00+08:00" in charts
    assert "WARN_BREADTH_MISSING" in charts
    assert "A股宽度缺失，暂不绘制趋势" in charts
    assert "!eligibleRows.length && !coreSignalRow" in charts
    assert "未绘制缺宽度趋势，仅标记已证明的CORE信号" in charts
    assert "个A股宽度缺失点已剔除 · 有效点连续绘制" in app
    assert "data: eligibleRows.map(row => ({" in charts


def test_temperature_track_exposes_core_tail_signal_and_persists_day_event() -> None:
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    app = (ROOT / "web/assets/app.js").read_text(encoding="utf-8")
    charts = (ROOT / "web/assets/charts.js").read_text(encoding="utf-8")

    assert 'id="temperatureCoreTailSignal"' in html
    assert "function renderTemperatureCoreTailSignal(payload)" in app
    assert "payload?.core_tail_signal" in app
    assert "summary.execute_triggered" in app
    assert "CORE尾盘买" in charts
    assert "DEGRADED_PASS" in app
    assert "CORE降级稳健尾盘买" in charts
    assert "core_tail_uncertainty" in charts
    assert "row.core_tail_status === 'EXECUTE'" in charts


def test_realtime_workflow_publishes_intraday_artifacts() -> None:
    assert {
        "data/calculated/intraday_temperature_history.csv",
        "data/site/intraday_temperature.json",
        "docs/data/intraday_temperature.json",
    }.issubset(set(ALLOWED_PATHS))


def test_pages_refresh_coalesces_active_workflows_and_waits_for_slow_publish() -> None:
    app = (ROOT / "web/assets/app.js").read_text(encoding="utf-8")
    assert "function findReusableGithubActionsRun(" in app
    assert "dispatch.reused" in app
    assert "maxWaitMs = 15 * 60 * 1000" in app


def test_refresh_renders_the_published_revision_without_stale_cache_or_lost_force_reload() -> None:
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    app = (ROOT / "web/assets/app.js").read_text(encoding="utf-8")
    assert "./assets/app.js?v=20260811-realtime-dispatch-v1" in html
    assert "function dashboardDataRevision(" in app
    assert "return [updateTime, buildTime, tradeDate]" in app
    assert "fetch(url, fresh ? { cache: 'no-store' } : undefined)" in app
    assert "loadJSON('./data/latest.json', { fresh: true })" in app
    assert "dashboardState.forceRefreshQueued = true" in app
    assert "do {" in app and "while (dashboardState.forceRefreshQueued)" in app
    assert "function dashboardMatchesPublishedRevision(" in app
    assert "syncDashboardToPublishedRevision(result)" in app
    assert "页面已同步最新数据" in app


def test_official_close_qvix_source_uses_official_payload() -> None:
    app = (ROOT / "web/assets/app.js").read_text(encoding="utf-8")
    assert "const qvix = nowcast.active ? nowcast : (latest?.official_close || {});" in app
    assert "const source = String(qvix.qvix_source || '');" in app


def test_flex_page_exposes_core_tail_alert_contract() -> None:
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    app = (ROOT / "web/assets/app.js").read_text(encoding="utf-8")

    assert 'id="flexCoreTailAlert"' in html
    assert 'id="dockFlexTailBadge"' in html
    assert "renderFlexCoreTailAlert(tailSignal)" in app
    assert "state === 'data_wait'" in app
    assert "缺失因子的RT可能范围" in app
    assert "本次采样无法量化而跳过" in app
    assert "其他信号仍按T+1" in app
