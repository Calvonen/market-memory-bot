from __future__ import annotations

import unittest

from trading_system.earnings_paper_lifecycle import (
    EarningsPaperLifecycleStatus,
    blocks_earnings_paper_execution,
    is_terminal_earnings_paper_status,
)


class EarningsPaperLifecycleTests(unittest.TestCase):
    def test_values_match_existing_runtime_contract(self) -> None:
        self.assertEqual(EarningsPaperLifecycleStatus.WAITING_ANALYSIS, "waiting_analysis")
        self.assertEqual(EarningsPaperLifecycleStatus.WAITING_APPROVAL, "waiting_approval")
        self.assertEqual(
            EarningsPaperLifecycleStatus.OBSERVING_POST_RELEASE,
            "observing_post_release",
        )
        self.assertEqual(
            EarningsPaperLifecycleStatus.WAITING_CONFIRMATION,
            "waiting_confirmation",
        )
        self.assertEqual(EarningsPaperLifecycleStatus.PAPER_EXECUTED, "paper_executed")
        self.assertEqual(EarningsPaperLifecycleStatus.EXPIRED_NO_TRADE, "expired_no_trade")
        self.assertEqual(EarningsPaperLifecycleStatus.FAILED, "failed")

    def test_only_final_outcomes_are_terminal(self) -> None:
        for status in (
            EarningsPaperLifecycleStatus.WAITING_ANALYSIS,
            EarningsPaperLifecycleStatus.WAITING_APPROVAL,
            EarningsPaperLifecycleStatus.OBSERVING_POST_RELEASE,
            EarningsPaperLifecycleStatus.WAITING_CONFIRMATION,
        ):
            self.assertFalse(is_terminal_earnings_paper_status(status))

        for status in (
            EarningsPaperLifecycleStatus.PAPER_EXECUTED,
            EarningsPaperLifecycleStatus.EXPIRED_NO_TRADE,
            EarningsPaperLifecycleStatus.FAILED,
        ):
            self.assertTrue(is_terminal_earnings_paper_status(status))

    def test_pre_execution_and_no_trade_outcomes_block_execution(self) -> None:
        for status in EarningsPaperLifecycleStatus:
            self.assertEqual(
                blocks_earnings_paper_execution(status),
                status is not EarningsPaperLifecycleStatus.PAPER_EXECUTED,
            )

    def test_unknown_status_fails_closed_instead_of_becoming_a_new_phase(self) -> None:
        with self.assertRaises(ValueError):
            is_terminal_earnings_paper_status("mystery_status")
        with self.assertRaises(ValueError):
            blocks_earnings_paper_execution("mystery_status")


if __name__ == "__main__":
    unittest.main()
