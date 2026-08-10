"""Published-artifact contracts for the browser-only Flex execution desk."""
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "web/assets/app.js"
DOCS_APP = ROOT / "docs/assets/app.js"


class FlexFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = WEB_APP.read_text(encoding="utf-8")
        cls.docs = DOCS_APP.read_text(encoding="utf-8")

    def test_published_app_matches_source(self) -> None:
        self.assertEqual(self.web, self.docs)

    def test_marking_is_not_capped_by_strategy_as_of(self) -> None:
        self.assertIn("function flexEffectiveMarkDate(positionCodes = [])", self.web)
        self.assertNotIn("function flexEffectiveMarkDate(preferredAsOf)", self.web)
        self.assertIn("const markAsOf = flexEffectiveMarkDate(relevantCodes);", self.web)
        self.assertIn("const marked = flexApplyEodMarksToLedger(ledger);", self.web)
        self.assertIn("complete_as_of", (ROOT / "src/core/etf_marks.py").read_text(encoding="utf-8"))

    def test_signal_actions_use_resolved_local_position_key(self) -> None:
        self.assertIn("function flexFindLocalPosition(item, ledger = loadFlexLedger())", self.web)
        self.assertIn("const localMatch = flexFindLocalPosition(item, ledger);", self.web)
        self.assertIn("const key = localMatch?.key || signalKey;", self.web)

    def test_capital_reduction_cannot_consume_invested_principal(self) -> None:
        self.assertIn("const cash = flexAvailableCash(ledger);", self.web)
        self.assertIn("if (delta < 0 && -delta > cash + 1e-6)", self.web)
        self.assertIn("下调全仓需先减仓或平仓", self.web)

    def test_intraday_quotes_are_display_only_with_two_sources(self) -> None:
        self.assertIn("https://qt.gtimg.cn/q=${symbols.join(',')}", self.web)
        self.assertIn("https://push2.eastmoney.com/api/qt/stock/get?secid=${market}.${c}", self.web)
        self.assertIn("function flexApplyDisplayMarksToLedger", self.web)
        self.assertIn("function flexPositionEodReturnPct", self.web)
        self.assertIn("const ret = flexPositionEodReturnPct(pos);", self.web)
        self.assertIn("function getFlexQuoteWindow", self.web)
        self.assertIn("const afterCloseMins = 15 * 60 + 16;", self.web)
        self.assertIn("phase: 'final'", self.web)
        self.assertIn("quoteEpochSeconds: epochSeconds", self.web)
        self.assertIn("f124", self.web)

    def test_execution_and_rebalance_contracts_are_explicit(self) -> None:
        self.assertIn("const FLEX_ONE_WAY_COST_RATE = 0.0001;", self.web)
        self.assertIn("const FLEX_ETF_LOT_SIZE = 100;", self.web)
        self.assertIn("function deskLocalRebalanceActions", self.web)
        self.assertIn("targetValue - currentValue", self.web)
        self.assertIn("待T+1开盘", self.web)
        self.assertIn("strictExecutionReady", self.web)
        self.assertIn("const marked = flexApplyEodMarksToLedger(ledger);", self.web)
        self.assertIn("kind: 'rebalance'", self.web)
        self.assertIn("function flexSimExecutePendingRebalance", self.web)
        self.assertIn("pending_rebalance", self.web)
        self.assertIn("type_cn: '模拟调仓减'", self.web)

    def test_satellite_risk_exit_is_basket_level_and_persistent(self) -> None:
        self.assertIn("function flexSatelliteBasketRiskStatus", self.web)
        self.assertIn("risk_exits:", self.web)
        self.assertIn("ledger.risk_exits[satSignalId]", self.web)
        self.assertIn("status: 'PENDING'", self.web)
        self.assertIn("下一交易日开盘整篮平仓", self.web)

    def test_simulation_is_incremental_and_read_only(self) -> None:
        self.assertIn("Incremental, read-only simulation ledger", self.web)
        self.assertIn("模拟仓为只读账本", self.web)
        self.assertIn("reset.disabled = sim", self.web)
        self.assertNotIn("version: 3,\n      book: 'sim'", self.web)

    def test_real_orders_are_durable_and_exit_actions_take_priority(self) -> None:
        self.assertIn("pending_orders: {}", self.web)
        self.assertIn("function flexSyncRealPendingOrders", self.web)
        self.assertIn("delete orders[`rebalance:${positionKey}`]", self.web)
        self.assertIn("pendingCloseKeys.has(order.position_key)", self.web)
        self.assertIn("function flexClearPendingOrder", self.web)

    def test_trade_modal_uses_display_mark_and_single_reduction_mode(self) -> None:
        index = (ROOT / "web/index.html").read_text(encoding="utf-8")
        self.assertIn("function flexExecutionReferencePrice", self.web)
        self.assertIn("const referencePrice = flexExecutionReferencePrice(ledger, key);", self.web)
        self.assertIn("id=\"flexModalReduceMode\"", index)
        self.assertIn("FlexExecutionCore.reductionInstruction", self.web)

    def test_stale_eod_decisions_and_non_aggressive_payloads_are_gated(self) -> None:
        self.assertIn("function flexEodDecisionGate", self.web)
        self.assertIn("if (!flexEodDecisionGate(marked, flex).ok)", self.web)
        self.assertIn("const mode = 'aggressive';", self.web)
        self.assertIn("flex = applyFlexModeOverlay(flex, 'aggressive');", self.web)

    def test_sim_avoid_is_advisory_and_badge_counts_clickable_rows(self) -> None:
        self.assertIn("策略回避提示·等待状态机确认", self.web)
        self.assertNotIn("回避·模拟自动归零", self.web)
        self.assertIn("row.querySelector('[data-flex-act]')", self.web)

    def test_holding_audit_fields_and_time_context_are_visible(self) -> None:
        index = (ROOT / "web/index.html").read_text(encoding="utf-8")
        self.assertIn("份额</span><span>成本/均价</span><span>盯市/时点", index)
        self.assertIn("策略信号：正式EOD", self.web)
        self.assertIn("收益/风控：持仓共同EOD", self.web)
        self.assertIn("const markSource = pos.mark_price_type === 'realtime'", self.web)

    def test_named_sector_positions_do_not_fall_through_to_shared_etf_code(self) -> None:
        self.assertIn("Named strategy intents must not fall through to code-only matching", self.web)
        self.assertIn("if (name) return null;", self.web)

    def test_core_tail_signal_is_backend_gated_and_core_only(self) -> None:
        self.assertIn("function flexCoreTailIsFresh(signal)", self.web)
        self.assertIn("function flexCoreTailActionableNow(signal)", self.web)
        self.assertIn("function flexWithCoreTailSignal(flex, signal)", self.web)
        self.assertIn("flexCoreTailWindowOpenNow(signal)", self.web)
        self.assertIn("execution_mode: 'T_TAIL_1450'", self.web)
        self.assertIn("etf_code: '510300'", self.web)
        self.assertIn("记尾盘买入", self.web)


if __name__ == "__main__":
    unittest.main()
