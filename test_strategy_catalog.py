"""
Tests for swing_research/strategy_catalog.py -- the self-registering
strategy architecture (item A of the continuous strategy pipeline, added
2026-08-23). Two things this must prove:

1. The catalog behaves EXACTLY like the hardcoded dicts it replaced
   (structural sanity + the same extra-column-naming regression coverage
   test_run_paper_trading.py's TestStrategyFactoryExtraColumnNaming
   already provides against run_paper_trading._STRATEGY_FACTORIES itself).
2. A NEW safeguard, per explicit direction: every strategy the LIVE
   deployment registry currently considers actively trading (PAPER_TRADING/
   PILOT_LIVE/PRODUCTION), other than PEAD (deliberately excluded -- see
   strategy_catalog.py's module docstring), must have an explicit entry in
   PAPER_TRADING_STRATEGY_SPECS. This is a TEST-TIME assertion only -- it
   does not add runtime auto-discovery or implicit registration; a strategy
   still only starts running because a human explicitly appended it to the
   catalog. Its purpose is to fail loudly, in the test suite, if a strategy
   is ever promoted to PAPER_TRADING in the registry without the matching
   catalog entry being added -- the exact class of gap
   deployment/PROMOTION_CHECKLIST.md was written to prevent, now checked
   automatically instead of only by manual checklist.
"""

import unittest

from swing_research.base import Strategy
from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS, RESEARCH_EXPERIMENT_SPECS

from deployment.base import DeploymentStatus
from deployment.deployment_manager import list_strategies

# PEAD is deliberately absent from PAPER_TRADING_STRATEGY_SPECS (event-
# driven, not a cross-sectional swing_research.base.Strategy -- see
# strategy_catalog.py's module docstring) and stays hardcoded directly in
# run_paper_trading.py instead.
_EXCLUDED_FROM_CATALOG_SAFEGUARD = {"pead"}


class TestPaperTradingCatalogStructure(unittest.TestCase):
    def test_no_duplicate_keys(self):
        keys = [spec.strategy_key for spec in PAPER_TRADING_STRATEGY_SPECS]
        self.assertEqual(len(keys), len(set(keys)), f"duplicate strategy_key(s) in PAPER_TRADING_STRATEGY_SPECS: {keys}")

    def test_every_spec_has_display_name_and_working_factory(self):
        for spec in PAPER_TRADING_STRATEGY_SPECS:
            with self.subTest(strategy_key=spec.strategy_key):
                self.assertTrue(spec.display_name)
                instance = spec.strategy_factory()
                self.assertIsInstance(instance, Strategy)


class TestPaperTradingCatalogExtraColumnNaming(unittest.TestCase):
    """Same regression this program already guards against in
    test_run_paper_trading.py's TestStrategyFactoryExtraColumnNaming, run
    directly against the catalog (the actual source of truth now) instead
    of through run_paper_trading._STRATEGY_FACTORIES."""

    EXPECTED_COLUMN_BY_STRATEGY = {
        "fifty_two_week_high_momentum": "nearness_percentile",
        "short_term_reversal": "reversal_percentile",
        "minervini_trend_template_filter": "rs_percentile",
        "cross_sectional_momentum": "momentum_percentile",
    }

    def test_extra_columns_are_named_what_precompute_expects(self):
        import pandas as pd

        dates = pd.bdate_range("2024-01-01", periods=280)
        data = {}
        for i, sym in enumerate(["AAA.NS", "BBB.NS", "CCC.NS"]):
            close = pd.Series([100.0 + i + j * 0.1 for j in range(280)], index=dates)
            data[sym] = pd.DataFrame({
                "Open": close, "High": close * 1.01, "Low": close * 0.98, "Close": close, "Volume": 100000,
            }, index=dates)

        specs_by_key = {spec.strategy_key: spec for spec in PAPER_TRADING_STRATEGY_SPECS}
        for strategy_key, expected_column in self.EXPECTED_COLUMN_BY_STRATEGY.items():
            with self.subTest(strategy_key=strategy_key):
                extra = specs_by_key[strategy_key].compute_extra_columns_fn(data)
                sample_series = next(iter(extra.values()))
                self.assertEqual(sample_series.name, expected_column)


class TestResearchExperimentCatalogStructure(unittest.TestCase):
    EXPECTED_KEYS = {
        "turtle_system2", "minervini_trend_template_filter", "52_week_high_momentum",
        "cross_sectional_momentum", "short_term_reversal", "betting_against_beta", "amihud_illiquidity",
        "max_effect",
    }

    def test_no_duplicate_keys(self):
        keys = [spec.strategy_key for spec in RESEARCH_EXPERIMENT_SPECS]
        self.assertEqual(len(keys), len(set(keys)), f"duplicate strategy_key(s) in RESEARCH_EXPERIMENT_SPECS: {keys}")

    def test_exactly_the_expected_keys_are_present(self):
        self.assertEqual({spec.strategy_key for spec in RESEARCH_EXPERIMENT_SPECS}, self.EXPECTED_KEYS)

    def test_every_runner_getter_resolves_to_a_callable(self):
        for spec in RESEARCH_EXPERIMENT_SPECS:
            with self.subTest(strategy_key=spec.strategy_key):
                self.assertTrue(spec.variant_description)
                fn = spec.runner_getter()
                self.assertTrue(callable(fn))


class TestLiveRegistryStrategiesHaveCatalogWiring(unittest.TestCase):
    """The safeguard requested alongside item A's approval: every strategy
    the LIVE deployment registry (deployment/state/strategy_registry.json)
    currently marks as actively trading must have production wiring in
    PAPER_TRADING_STRATEGY_SPECS, PEAD excepted. Reads the REAL registry
    (read-only -- list_strategies() only reads the JSON file, never writes
    it) so this actually guards the system that runs, not a fixture."""

    def test_every_active_non_pead_strategy_is_in_the_paper_trading_catalog(self):
        catalog_keys = {spec.strategy_key for spec in PAPER_TRADING_STRATEGY_SPECS}
        active_statuses = (DeploymentStatus.PAPER_TRADING, DeploymentStatus.PILOT_LIVE, DeploymentStatus.PRODUCTION)

        missing = []
        for record in list_strategies():
            if record.strategy_key in _EXCLUDED_FROM_CATALOG_SAFEGUARD:
                continue
            if record.deployment_status in active_statuses and record.strategy_key not in catalog_keys:
                missing.append((record.strategy_key, record.deployment_status.value))

        self.assertEqual(
            missing, [],
            f"Strategies registered as actively trading but missing from "
            f"PAPER_TRADING_STRATEGY_SPECS (production wiring was never added -- "
            f"see deployment/PROMOTION_CHECKLIST.md item 6): {missing}"
        )


if __name__ == "__main__":
    unittest.main()
