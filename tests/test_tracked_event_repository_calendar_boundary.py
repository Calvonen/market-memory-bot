from __future__ import annotations

from datetime import UTC, datetime
import unittest

from trading_system.tracked_event_repository import (
    SupabaseTrackedEventRepository,
    TrackedEventTimeStatus,
)


class _NoRpcClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, payload: dict[str, object]):
        self.calls.append((name, payload))
        raise AssertionError("calendar-bound repository call must fail before RPC")


class TrackedEventRepositoryCalendarBoundaryTests(unittest.TestCase):
    def test_calendar_binding_fails_before_rpc(self) -> None:
        client = _NoRpcClient()
        repo = SupabaseTrackedEventRepository(client)

        with self.assertRaisesRegex(ValueError, "use calendar promotion"):
            repo.upsert(
                company_name="Example Plc",
                instrument="EXAMPLE",
                market="USA",
                source="calendar",
                external_key="calendar:123",
                kind="earnings",
                title="Example Plc earnings",
                event_at=datetime(2026, 8, 28, 12, tzinfo=UTC),
                event_time_status=TrackedEventTimeStatus.CONFIRMED,
                actor="test",
                calendar_event_id="123",
            )

        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
