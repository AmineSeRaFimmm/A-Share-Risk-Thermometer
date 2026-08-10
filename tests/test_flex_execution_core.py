from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
WEB_CORE = ROOT / "web/assets/flex_execution_core.js"
DOCS_CORE = ROOT / "docs/assets/flex_execution_core.js"


def _run_core(script: str) -> object:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_published_execution_core_matches_source() -> None:
    assert WEB_CORE.read_bytes() == DOCS_CORE.read_bytes()


def test_etf_orders_are_lot_sized_and_cash_bounded() -> None:
    result = _run_core(
        """
        const c = require('./web/assets/flex_execution_core.js');
        const buy = c.buyOrderFromBudget(10000, 4.123, 10000);
        const tooSmall = c.buyOrderFromBudget(400, 4.123, 10000);
        const partial = c.sellQuantity(1050, 4.2, {pct: 50});
        const full = c.sellQuantity(1050, 4.2, {pct: 100});
        process.stdout.write(JSON.stringify({buy, tooSmall, partial, full}));
        """
    )
    assert result["buy"]["qty"] % 100 == 0
    assert result["buy"]["cash_required"] <= 10000
    assert result["tooSmall"]["qty"] == 0
    assert result["partial"] == 500
    assert result["full"] == 1050


def test_quote_timestamp_and_journal_order_are_behavioral() -> None:
    result = _run_core(
        """
        const c = require('./web/assets/flex_execution_core.js');
        const now = 2000000000 * 1000;
        const base = {nowMs: now, quoteYmd: '2033-05-18', todayYmd: '2033-05-18'};
        const fresh = c.quoteTimestampIsUsable({...base, quoteEpochSeconds: 2000000000 - 60, phase: 'intraday'});
        const staleLive = c.quoteTimestampIsUsable({...base, quoteEpochSeconds: 2000000000 - 600, phase: 'intraday'});
        const final = c.quoteTimestampIsUsable({...base, quoteEpochSeconds: 2000000000 - 600, phase: 'final'});
        const wrongDay = c.quoteTimestampIsUsable({...base, quoteYmd: '2033-05-17', quoteEpochSeconds: 2000000000 - 60, phase: 'final'});
        const rows = c.sortJournalNewestFirst([
          {id: 'old', ts: '2026-08-03T09:30:00+08:00'},
          {id: 'new', ts: '2026-08-05T09:30:00+08:00'},
          {id: 'mid', trade_date: '2026-08-04'},
        ]).map(row => row.id);
        process.stdout.write(JSON.stringify({fresh, staleLive, final, wrongDay, rows}));
        """
    )
    assert result == {
        "fresh": True,
        "staleLive": False,
        "final": True,
        "wrongDay": False,
        "rows": ["new", "mid", "old"],
    }


def test_execution_price_reduction_mode_and_eod_gate_are_behavioral() -> None:
    result = _run_core(
        """
        const c = require('./web/assets/flex_execution_core.js');
        const price = c.firstPositivePrice([null, 0, 4.709, 4.1]);
        const byAmount = c.reductionInstruction('amount', 12000, 50);
        const byPct = c.reductionInstruction('pct', 12000, 35);
        const fresh = c.eodDecisionGate({markDate: '2026-08-07', requiredDate: '2026-08-07'});
        const staleDate = c.eodDecisionGate({markDate: '2026-08-06', requiredDate: '2026-08-07'});
        const staleCode = c.eodDecisionGate({markDate: '2026-08-07', requiredDate: '2026-08-07', staleCount: 1});
        const missing = c.eodDecisionGate({markDate: '2026-08-07', requiredDate: '2026-08-07', missing: 1});
        process.stdout.write(JSON.stringify({price, byAmount, byPct, fresh, staleDate, staleCode, missing}));
        """
    )
    assert result == {
        "price": 4.709,
        "byAmount": {"amount": 12000, "pct": None},
        "byPct": {"amount": None, "pct": 35},
        "fresh": {"ok": True, "code": "OK"},
        "staleDate": {"ok": False, "code": "STALE"},
        "staleCode": {"ok": False, "code": "STALE"},
        "missing": {"ok": False, "code": "MISSING"},
    }
