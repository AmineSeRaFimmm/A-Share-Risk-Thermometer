"""Static contracts for the browser-only Flex execution desk.

The desk intentionally has no build step or JavaScript test runner. These checks
guard the user-facing invariants in both the source and GitHub Pages artifacts.
"""
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
        self.assertIn("function flexEffectiveMarkDate()", self.web)
        self.assertNotIn("function flexEffectiveMarkDate(preferredAsOf)", self.web)
        self.assertIn("const markAsOf = flexEffectiveMarkDate();", self.web)
        self.assertIn("const marked = flexApplyEodMarksToLedger(ledger);", self.web)

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


if __name__ == "__main__":
    unittest.main()
