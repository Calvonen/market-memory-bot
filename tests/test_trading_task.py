from __future__ import annotations

import unittest
from datetime import UTC, datetime

from trading_system.models import TradingMode
from trading_system.trading_task import CanonicalTradingTask, TradingTaskState


NOW = datetime(2026, 8, 30, 18, 0, tzinfo=UTC)


def task(**overrides) -> CanonicalTradingTask:
    values = {
        "task_id": "task-1",
        "tracked_event_id": "event-1",
        "source_event_id": "tracked:event-1",
        "instrument": "EXM.ASX",
        "mode": TradingMode.PAPER,
        "state": TradingTaskState.PENDING,
        "created_by": "tester",
        "created_at": NOW,
    }
    values.update(overrides)
    return CanonicalTradingTask(**values)


class TradingTaskTests(unittest.TestCase):
    def test_pending_task_is_explicit_and_normalized(self) -> None:
        created = task(instrument=" exm.asx ")
        self.assertEqual(created.instrument, "EXM.ASX")
        self.assertIs(created.state, TradingTaskState.PENDING)

    def test_pending_task_rejects_approval_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "pending trading task"):
            task(approved_by="approver", approved_at=NOW)

    def test_approved_task_requires_approval_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires approval metadata"):
            task(state=TradingTaskState.APPROVED)

    def test_approved_task_accepts_explicit_approval(self) -> None:
        approved = task(
            state=TradingTaskState.APPROVED,
            approved_by="approver",
            approved_at=NOW,
        )
        self.assertEqual(approved.approved_by, "approver")

    def test_cancelled_task_requires_cancellation_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires cancellation metadata"):
            task(state=TradingTaskState.CANCELLED)

    def test_naive_audit_timestamp_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "created_at must be timezone-aware"):
            task(created_at=datetime(2026, 8, 30, 18, 0))


if __name__ == "__main__":
    unittest.main()
