from __future__ import annotations

import unittest

from trading_system.earnings_paper_lifecycle import EarningsPaperLifecycleStatus
from trading_system.post_release_paper import PostReleasePaperResult


class PostReleasePaperResultLifecycleTests(unittest.TestCase):
    def test_existing_string_status_is_normalized_without_changing_contract(self) -> None:
        result = PostReleasePaperResult("waiting_confirmation", "waiting")

        self.assertIs(
            result.status,
            EarningsPaperLifecycleStatus.WAITING_CONFIRMATION,
        )
        self.assertEqual(result.status, "waiting_confirmation")

    def test_unknown_status_fails_closed_at_result_boundary(self) -> None:
        with self.assertRaises(ValueError):
            PostReleasePaperResult("mystery_status", "invalid")


if __name__ == "__main__":
    unittest.main()
