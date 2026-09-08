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


def test_quantity_buy_is_exact_and_rejects_invalid_or_unfunded_orders():
    result = _run_core("""
        const c = require('./web/assets/flex_execution_core.js');
        const valid = c.buyOrderFromQuantity(2500, 4, 10001);
        const rejected = [[0,4,10001],[150,4,10001],[100.5,4,10001],
          [2500,4,10000],[100,NaN,10001],[100,Infinity,10001]].map(args => {
          try { c.buyOrderFromQuantity(...args); return false; } catch (_) { return true; }
        });
        process.stdout.write(JSON.stringify({valid, rejected}));
    """)
    assert result["valid"] == {"qty": 2500, "gross": 10000, "fee": 1, "cash_required": 10001}
    assert all(result["rejected"])


def test_allocation_sells_before_buying_and_missing_prices_block_entire_batch():
    result = _run_core("""
        const c = require('./web/assets/flex_execution_core.js');
        const args = {cash: 0, positions: {sat: {qty: 10000}},
          targets: [{key: 'core', weight: .6}, {key: 'sat', weight: .4}],
          prices: {sat: 1.1, core: 2}};
        process.stdout.write(JSON.stringify({good: c.allocationBatch(args),
          bad: c.allocationBatch({...args, prices: {core: 2}})}));
    """)
    batch = result["good"]
    assert batch["ok"]
    assert batch["nav"] == 11000
    assert batch["cash"] >= 0
    assert batch["trades"][0]["side"] == "SELL"
    assert batch["trades"][1]["side"] == "BUY"
    assert result["bad"] == {"ok": False, "code": "MISSING_EXECUTION_PRICE"}


def test_tail_execution_never_falls_back_to_daily_open():
    result = _run_core("""
        const c = require('./web/assets/flex_execution_core.js');
        const args = {day: '2026-08-06', phase: 'tail_1450', code: '510300',
          executionAt: '2026-08-06T14:51:00+08:00',
          bar: {trade_date: '2026-08-06', open: 100},
          evidence: {timestamp: '2026-08-06T14:51:00+08:00', price: 105,
            etf_code: '510300', source: 'fixture', price_type: 'tail_1450'}};
        process.stdout.write(JSON.stringify({valid: c.executionPrice(args),
          absent: c.executionPrice({...args, evidence: null}),
          stale: c.executionPrice({...args, executionAt: '2026-08-06T14:52:00+08:00'})}));
    """)
    assert result == {"valid": 105, "absent": None, "stale": None}


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


def test_execution_dates_quotes_and_labels_are_behavioral() -> None:
    result = _run_core(
        """
        const c = require('./web/assets/flex_execution_core.js');
        const calendar = ['2026-08-07', '2026-08-10', '2026-08-11'];
        const valid = c.validateTradeDate({tradeDate: '2026-08-10', sessionDate: '2026-08-11', calendar, notBefore: '2026-08-10'});
        const weekend = c.validateTradeDate({tradeDate: '2026-08-09', sessionDate: '2026-08-11', calendar});
        const early = c.validateTradeDate({tradeDate: '2026-08-07', sessionDate: '2026-08-11', calendar, notBefore: '2026-08-10'});
        const future = c.validateTradeDate({tradeDate: '2026-08-12', sessionDate: '2026-08-11', calendar});
        const snapshot = {fetchedAt: 1000, quotes: {'510300': {price: 4.75, quote_date: '2026-08-11'}}};
        const fresh = c.freshQuote(snapshot, '510300', {active: true, nowMs: 1500, todayYmd: '2026-08-11', maxAgeMs: 1000});
        const stale = c.freshQuote(snapshot, '510300', {active: true, nowMs: 2500, todayYmd: '2026-08-11', maxAgeMs: 1000});
        const wrongDay = c.freshQuote(snapshot, '510300', {active: true, nowMs: 1500, todayYmd: '2026-08-12', maxAgeMs: 1000});
        const labels = {
          t: c.openExecutionLabel({lag: 0}),
          t1: c.openExecutionLabel({lag: 1}),
          tail: c.openExecutionLabel({lag: 0, tail: true}),
          generic: c.satelliteCloseLabel({phase: 'pending', genericClosing: true}),
          stop: c.satelliteCloseLabel({closeCode: 'LOCAL_STOP_LOSS', phase: 'real_pending', genericClosing: true}),
          take: c.satelliteCloseLabel({closeCode: 'LOCAL_TAKE_PROFIT', phase: 'executed', genericClosing: true}),
        };
        process.stdout.write(JSON.stringify({valid, weekend, early, future, fresh, stale, wrongDay, labels}));
        """
    )
    assert result == {
        "valid": {"ok": True, "code": "OK"},
        "weekend": {"ok": False, "code": "NON_TRADING_DAY"},
        "early": {"ok": False, "code": "BEFORE_EXECUTION"},
        "future": {"ok": False, "code": "FUTURE"},
        "fresh": {"price": 4.75, "quote_date": "2026-08-11"},
        "stale": None,
        "wrongDay": None,
        "labels": {
            "t": "待T+1开盘",
            "t1": "T+1可确认",
            "tail": "14:50尾盘买",
            "generic": "策略待平",
            "stop": "止损待平",
            "take": "止盈已执行",
        },
    }


def test_satellite_history_starts_at_latest_real_composition_change() -> None:
    result = _run_core(
        """
        const c = require('./web/assets/flex_execution_core.js');
        const positions = [
          {key: 'coal', name: '煤炭', etf_code: '515220', sleeve: 'satellite', buy_date: '2026-08-05'},
          {key: 'metal', name: '有色金属', etf_code: '512400', sleeve: 'satellite', buy_date: '2026-08-05'},
        ];
        const journal = [
          {type: 'OPEN', name: '煤炭', etf_code: '515220', trade_date: '2026-08-05'},
          {type: 'ADD', name: '煤炭', etf_code: '515220', trade_date: '2026-08-07'},
          {type: 'CLOSE', name: '传媒', etf_code: '512980', trade_date: '2026-08-10'},
          {type: 'REDUCE', name: '沪深300', etf_code: '510300', trade_date: '2026-08-11'},
        ];
        const start = c.satelliteHistoryStartDate(positions, journal, '2026-08-06');
        process.stdout.write(JSON.stringify({start}));
        """
    )
    assert result == {"start": "2026-08-10"}
