from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

from trading_system.tracked_event_pre_event_context_repository import (
    capture_pre_event_market_context,
)
from trading_system.tracked_event_repository import SupabaseTrackedEventRepository


class _RpcCall:
    def __init__(self, *, data: object = None, error: Exception | None = None) -> None:
        self.data = data
        self.error = error

    def execute(self) -> SimpleNamespace:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=self.data)


class _Client:
    def __init__(self, *, data: object = None, error: Exception | None = None) -> None:
        self.data = data
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, payload: dict[str, object]) -> _RpcCall:
        self.calls.append((name, payload))
        return _RpcCall(data=self.data, error=self.error)


class TrackedEventPreEventContextRepositoryTests(unittest.TestCase):
    @staticmethod
    def _row() -> dict[str, object]:
        return {
            "id": "11111111-1111-1111-1111-111111111111",
            "tracked_instrument_id": "22222222-2222-2222-2222-222222222222",
            "calendar_event_id": None,
            "company_name": "Example plc",
            "instrument": "EXM.L",
            "market": "LSE",
            "source": "calendar",
            "external_key": "example-results",
            "kind": "earnings",
            "title": "Example results",
            "event_at": datetime(2026, 8, 25, 6, 0, tzinfo=UTC),
            "event_time_status": "confirmed",
            "status": "tracked",
        }

    @staticmethod
    def _snapshot() -> dict[str, object]:
        return {
            "schema_version": 1,
            "session_date": "2026-08-24",
            "previous_session_date": "2026-08-21",
            "open_price": "100",
            "high_price": "103",
            "low_price": "99",
            "close_price": "102",
            "previous_close_price": "101",
            "session_return_pct": "2.00",
            "close_to_close_return_pct": "0.9900990099009900990099009900",
            "close_to_close_direction": "up",
        }

    def test_calls_exact_capture_rpc_and_returns_persisted_event(self) -> None:
        client = _Client(data=self._row())
        repository = SupabaseTrackedEventRepository(client)
        snapshot = self._snapshot()

        event = capture_pre_event_market_context(
            repository,
            event_id="11111111-1111-1111-1111-111111111111",
            snapshot=snapshot,
            market_timezone="Europe/London",
            actor="tracked-event-worker",
        )

        self.assertEqual(event.event_id, "11111111-1111-1111-1111-111111111111")
        self.assertEqual(
            client.calls,
            [
                (
                    "capture_tracked_market_event_pre_event_context",
                    {
                        "input_event_id": "11111111-1111-1111-1111-111111111111",
                        "input_pre_event_market_context": snapshot,
                        "input_market_timezone": "Europe/London",
                        "input_actor": "tracked-event-worker",
                    },
                )
            ],
        )

    def test_translates_different_persisted_context_conflict(self) -> None:
        client = _Client(error=Exception("tracked_market_event_pre_event_context_locked"))
        repository = SupabaseTrackedEventRepository(client)

        with self.assertRaisesRegex(RuntimeError, "different pre_event_market_context"):
            capture_pre_event_market_context(
                repository,
                event_id="11111111-1111-1111-1111-111111111111",
                snapshot=self._snapshot(),
                market_timezone="Europe/London",
                actor="tracked-event-worker",
            )


if __name__ == "__main__":
    unittest.main()
