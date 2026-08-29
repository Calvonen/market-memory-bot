from __future__ import annotations

import unittest
from datetime import date

from trading_system.calendar_release_worker import (
    CalendarReleaseTarget,
    SupabaseCalendarReleaseTargetRepository,
)
from trading_system.workflow_readiness_evidence_loader import (
    SupabaseWorkflowReadinessEvidenceLoader,
)


class _Response:
    def __init__(self, data):
        self.data = data


class _Rpc:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return _Response(self.data)


class _RpcClient:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _Rpc(self.data)


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []
        self.limit_count = None

    def select(self, _fields):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, _field, desc=False):
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    def execute(self):
        rows = [
            row
            for row in self.rows
            if all(row.get(field) == value for field, value in self.filters)
        ]
        if self.limit_count is not None:
            rows = rows[: self.limit_count]
        return _Response(rows)


class _TableClient:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _Query(self.rows if name == "tracked_event_workflow_blockers" else [])


class PreShellWorkflowBlockerTests(unittest.TestCase):
    def test_release_shell_repository_surfaces_persisted_binding_blocker_code(self):
        client = _RpcClient(
            [
                {
                    "out_release_event_id": None,
                    "out_blocker_code": "tracked_release_calendar_binding_identity_conflict",
                }
            ]
        )
        repository = SupabaseCalendarReleaseTargetRepository(client)
        target = CalendarReleaseTarget(
            calendar_event_id="calendar-1",
            event_id="calendar:calendar-1",
            ticker="EXM",
            scheduled_date=date(2026, 8, 29),
            market="NASDAQ",
            tracked_event_id="11111111-1111-1111-1111-111111111111",
        )

        with self.assertRaisesRegex(
            RuntimeError, "tracked_release_calendar_binding_identity_conflict"
        ):
            repository.ensure_release_shell(target)

        self.assertEqual(
            client.calls,
            [
                (
                    "ensure_tracked_event_release_shell_with_blocker",
                    {"input_tracked_event_id": target.tracked_event_id},
                )
            ],
        )

    def test_active_tracked_release_blocker_is_visible_without_release_shell(self):
        loader = SupabaseWorkflowReadinessEvidenceLoader(
            _TableClient(
                [
                    {
                        "tracked_market_event_id": "tracked-123",
                        "step_key": "release",
                        "blocker_code": "tracked_release_calendar_binding_identity_conflict",
                        "resolved_at": None,
                    }
                ]
            )
        )

        self.assertTrue(loader._tracked_release_blocker("tracked-123"))

    def test_resolved_tracked_release_blocker_is_not_active(self):
        loader = SupabaseWorkflowReadinessEvidenceLoader(
            _TableClient(
                [
                    {
                        "tracked_market_event_id": "tracked-123",
                        "step_key": "release",
                        "blocker_code": "tracked_release_calendar_binding_identity_conflict",
                        "resolved_at": "2026-08-29T02:30:00+00:00",
                    }
                ]
            )
        )

        self.assertFalse(loader._tracked_release_blocker("tracked-123"))


if __name__ == "__main__":
    unittest.main()
