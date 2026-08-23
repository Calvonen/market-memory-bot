from __future__ import annotations

import math
import unittest
from decimal import Decimal
from types import SimpleNamespace

from scripts.scheduled_news_paper_demo import (
    _minimum_equity_for_one_unit,
    _require_finite,
    _spread_pct_from_quote,
    _trade_levels,
)
from trading_system.models import Direction
from trading_system.risk import RiskConfig


class ScheduledNewsPaperDemoTests(unittest.TestCase):
    def test_trigger_validation_rejects_nan_and_infinity(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    _require_finite(value, name="--trigger-pct", minimum=0.0)

    def test_spread_uses_current_quote_values(self) -> None:
        narrow = SimpleNamespace(bid=Decimal("99.9"), ask=Decimal("100.1"))
        wide = SimpleNamespace(bid=Decimal("99"), ask=Decimal("101"))
        self.assertLess(_spread_pct_from_quote(narrow), _spread_pct_from_quote(wide))
        self.assertAlmostEqual(_spread_pct_from_quote(wide), 2.0)

    def test_minimum_equity_guarantees_one_unit_under_default_position_cap(self) -> None:
        config = RiskConfig()
        levels = _trade_levels(150000.0, Direction.LONG)
        required = _minimum_equity_for_one_unit(levels, config)
        max_position_value = required * (config.max_position_pct / 100.0)
        max_risk_amount = required * (config.max_risk_per_trade_pct / 100.0)
        self.assertGreater(max_position_value, levels.entry)
        self.assertGreater(max_risk_amount, abs(levels.entry - levels.stop))

    def test_demo_trade_levels_are_risk_geometry_only(self) -> None:
        levels = _trade_levels(100.0, Direction.LONG)
        self.assertEqual(levels.entry, 100.0)
        self.assertAlmostEqual(levels.stop, 99.75)
        self.assertAlmostEqual(levels.target_1, 100.5)


if __name__ == "__main__":
    unittest.main()
