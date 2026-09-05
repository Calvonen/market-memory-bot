from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from tests.test_post_release_confirmation_fallback import (
    ANCHOR,
    _event,
    _flat_anchor,
    _observation_close,
    _reaction,
    _run,
)
from trading_system.earnings_paper_lifecycle import EarningsPaperLifecycleStatus


class EarningsConfirmationExpiryWiringTests(unittest.TestCase):
    def test_completed_horizon_without_qualifying_reaction_expires_no_trade(self) -> None:
        terminal_flat = _reaction(
            interval_minutes=15,
            candle_start=ANCHOR + timedelta(hours=7, minutes=30),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            result = _run((_flat_anchor(), _observation_close(), terminal_flat))

        self.assertIs(result.status, EarningsPaperLifecycleStatus.EXPIRED_NO_TRADE)
        self.assertEqual(result.status, "expired_no_trade")
        run_paper.assert_not_called()

    def test_incomplete_horizon_still_waits_for_confirmation(self) -> None:
        early_flat = _reaction(
            interval_minutes=15,
            candle_start=ANCHOR + timedelta(hours=7, minutes=15),
            close_price=Decimal("100.10"),
            direction="flat",
        )

        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            result = _run((_flat_anchor(), _observation_close(), early_flat))

        self.assertIs(result.status, EarningsPaperLifecycleStatus.WAITING_CONFIRMATION)
        self.assertEqual(result.status, "waiting_confirmation")
        run_paper.assert_not_called()

    def test_legacy_event_without_snapshot_cannot_gain_expiry_authority(self) -> None:
        terminal_flat = _reaction(
            interval_minutes=15,
            candle_start=ANCHOR + timedelta(hours=7, minutes=30),
            close_price=Decimal("100.10"),
            direction="flat",
        )
        legacy_event = _event(tracking_config_snapshot=None)

        with patch(
            "trading_system.tracked_event_paper_bridge.run_post_release_paper"
        ) as run_paper:
            result = _run(
                (_flat_anchor(), _observation_close(), terminal_flat),
                tracked_event=legacy_event,
            )

        self.assertIs(result.status, EarningsPaperLifecycleStatus.WAITING_CONFIRMATION)
        self.assertEqual(result.status, "waiting_confirmation")
        run_paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
