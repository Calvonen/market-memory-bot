from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260903156000_broker_attempt_cancel_guard.sql"


class BrokerAttemptCancelGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_unresolved_attempt_blocks_task_cancellation(self) -> None:
        self.assertIn("event_paper_broker_attempts", self.sql)
        self.assertIn("trading_task_broker_attempt_unresolved", self.sql)
        self.assertIn("run.status in ('paper_executed', 'expired_no_trade')", self.sql)

    def test_cancel_uses_same_event_lock_order(self) -> None:
        first = self.sql.index("hashtextextended(source_event, 1)")
        second = self.sql.index("hashtextextended(source_event, 0)")
        self.assertLess(first, second)


if __name__ == "__main__":
    unittest.main()
