from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from tests.test_post_release_confirmation_fallback import (
    ANCHOR,
    _flat_anchor,
    _observation_close,
    _reaction,
    _run,
)
from trading_system.earnings_paper_lifecycle import EarningsPaperLifecycleStatus


class PostReleaseConfirmationFallbackLifecycleTests(unittest.TestCase):
    def test_all_flat_post_30m_reactions_return_canonical_waiting_confirmation(self) -> None:
        later_flat = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(minutes=30),
            close_price=Decimal("100.10"),
            direction="flat",
        )
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            result = _run((_flat_anchor(), _observation_close(), later_flat))

        self.assertIs(result.status, EarningsPaperLifecycleStatus.WAITING_CONFIRMATION)
        self.assertEqual(result.status, "waiting_confirmation")
        run_paper.assert_not_called()

    def test_post_30m_reaction_outside_horizon_keeps_canonical_waiting_confirmation(self) -> None:
        later = _reaction(
            interval_minutes=5,
            candle_start=ANCHOR + timedelta(hours=9),
            close_price=Decimal("102"),
            direction="positive",
        )
        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            result = _run((_flat_anchor(), _observation_close(), later))

        self.assertIs(result.status, EarningsPaperLifecycleStatus.WAITING_CONFIRMATION)
        self.assertEqual(result.status, "waiting_confirmation")
        run_paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
