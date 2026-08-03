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
    assert "个A股宽度缺失点已剔除 · 有效点连续绘制" in app
    assert "data: eligibleRows.map(row => ({" in charts


def test_realtime_workflow_publishes_intraday_artifacts() -> None:
    assert {
        "data/calculated/intraday_temperature_history.csv",
        "data/site/intraday_temperature.json",
        "docs/data/intraday_temperature.json",
    }.issubset(set(ALLOWED_PATHS))
