"""
Mock-based unit tests for research_lab/portfolio_manager.py. Confirms
default allocation (100/0, virtual capital only) and -- critically --
that this module never imports the real risk/risk_manager.py or
portfolio/portfolio_manager.py, per the isolation requirement. Run with:

    python test_portfolio_manager_research_lab.py
"""

import sys
import unittest
from unittest.mock import patch

from research_lab import portfolio_manager as pm


class TestPortfolioManagerResearchLab(unittest.TestCase):
    def test_default_intraday_allocation_is_zero_capital(self):
        # Uses the real config.settings defaults (INTRADAY_CAPITAL_ALLOCATION_PCT=0)
        self.assertEqual(pm.get_intraday_research_capital(), 0.0)

    def test_default_swing_allocation_is_100_pct(self):
        self.assertEqual(pm.get_swing_capital_allocation_pct(), 100)

    def test_not_promoted_to_production_by_default(self):
        self.assertFalse(pm.is_intraday_promoted_to_production())

    @patch("research_lab.portfolio_manager.settings")
    def test_capital_scales_with_allocation_pct(self, mock_settings):
        mock_settings.RESEARCH_LAB_VIRTUAL_CAPITAL = 100000
        mock_settings.INTRADAY_CAPITAL_ALLOCATION_PCT = 20
        self.assertEqual(pm.get_intraday_research_capital(), 20000.0)

    @patch("research_lab.portfolio_manager.settings")
    def test_promoted_when_allocation_above_zero(self, mock_settings):
        mock_settings.INTRADAY_CAPITAL_ALLOCATION_PCT = 20
        self.assertTrue(pm.is_intraday_promoted_to_production())

    def test_never_imports_real_swing_risk_or_portfolio_manager(self):
        # Isolation check: research_lab.portfolio_manager's module-level
        # imports must not include the real swing components.
        module = sys.modules["research_lab.portfolio_manager"]
        source_imports = [name for name in dir(module) if not name.startswith("_")]
        self.assertNotIn("risk_manager", source_imports)
        with open(module.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("risk.risk_manager", source)
        self.assertNotIn("portfolio.portfolio_manager", source)


if __name__ == "__main__":
    unittest.main()
