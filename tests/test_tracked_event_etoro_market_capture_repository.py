from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

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


class TrackedEventEtoroMarketCaptureRepositoryTests(unittest.TestCase):
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
            "resolved_etoro_instrument_id": 123,
            "resolved_etoro_symbol": "EXM.L",
            "resolved_etoro_display_name": "Example plc",
            "resolved_etoro_market": "London",
        }

    def test_calls_exact_capture_rpc_and_returns_persisted_event(self) -> None:
        client = _Client(data=self._row())
        repository = SupabaseTrackedEventRepository(client)

        event = repository.capture_resolved_etoro_market(
            event_id="11111111-1111-1111-1111-111111111111",
            etoro_instrument_id=123,
            etoro_symbol="EXM.L",
            etoro_display_name="Example plc",
            etoro_market="London",
            actor="tracked-event-worker",
        )

        self.assertEqual(event.resolved_etoro_market, "London")
        self.assertEqual(
            client.calls,
            [
                (
                    "capture_tracked_market_event_resolved_market",
                    {
                        "input_event_id": "11111111-1111-1111-1111-111111111111",
                        "input_etoro_instrument_id": 123,
                        "input_etoro_symbol": "EXM.L",
                        "input_etoro_display_name": "Example plc",
                        "input_etoro_market": "London",
                        "input_actor": "tracked-event-worker",
                    },
                )
            ],
        )

    def test_translates_different_persisted_market_conflict(self) -> None:
        client = _Client(error=Exception("tracked_market_event_resolved_market_conflict"))
        repository = SupabaseTrackedEventRepository(client)

        with self.assertRaisesRegex(RuntimeError, "different resolved_etoro_market"):
            repository.capture_resolved_etoro_market(
                event_id="11111111-1111-1111-1111-111111111111",
                etoro_instrument_id=123,
                etoro_symbol="EXM.L",
                etoro_display_name="Example plc",
                etoro_market="London",
                actor="tracked-event-worker",
            )


if __name__ == "__main__":
    unittest.main()
