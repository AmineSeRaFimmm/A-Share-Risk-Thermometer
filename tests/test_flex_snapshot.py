from __future__ import annotations

import json

from src.core.flex_snapshot import build_flex_snapshot, publish_flex_snapshot


def _payloads():
    return (
        {"as_of": "2026-08-10", "flex_panel": {"all_actions": []}},
        {
            "as_of": "2026-08-10",
            "complete_as_of": "2026-08-10",
            "quality": "OK",
            "by_code": {},
        },
        {
            "authoritative": True,
            "coverage_through": "2026-12-31",
            "dates": ["2026-08-10"],
        },
    )


def test_flex_snapshot_revision_is_content_addressed():
    first = build_flex_snapshot(*_payloads())
    second = build_flex_snapshot(*_payloads())
    assert first["revision"] == second["revision"]
    changed = _payloads()
    changed[1]["quality"] = "WARN"
    assert build_flex_snapshot(*changed)["revision"] != first["revision"]
    assert first["schema_version"] == 2
    assert first["daily_strategy_brief"]["strategy_id"] == "FLEX_AGGRESSIVE"
    assert first["daily_strategy_brief"]["provenance"]["browser_ledger_used"] is False


def test_publish_flex_snapshot_writes_identical_site_and_docs(tmp_path):
    site = tmp_path / "site"
    docs = tmp_path / "docs"
    site.mkdir()
    docs.mkdir()
    playbook, marks, calendar = _payloads()
    for name, payload in [
        ("stage_playbook.json", playbook),
        ("etf_daily_marks.json", marks),
        ("trade_calendar.json", calendar),
    ]:
        (site / name).write_text(json.dumps(payload), encoding="utf-8")

    snapshot = publish_flex_snapshot(site_dir=site, docs_dir=docs)

    assert json.loads((site / "flex_snapshot.json").read_text()) == snapshot
    assert (site / "flex_snapshot.json").read_bytes() == (
        docs / "data" / "flex_snapshot.json"
    ).read_bytes()
