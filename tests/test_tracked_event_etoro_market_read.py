from datetime import UTC, datetime
import unittest

from trading_system.tracked_event_repository import SupabaseTrackedEventRepository


class TrackedEventEtoroMarketReadTests(unittest.TestCase):
    @staticmethod
    def _row(**overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
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
        row.update(overrides)
        return row

    def test_reads_resolved_etoro_market_when_present(self) -> None:
        event = SupabaseTrackedEventRepository._row_to_event(
            self._row(resolved_etoro_market="London")
        )

        self.assertEqual(event.resolved_etoro_market, "London")

    def test_missing_or_null_resolved_etoro_market_stays_none(self) -> None:
        missing = SupabaseTrackedEventRepository._row_to_event(self._row())
        explicit_null = SupabaseTrackedEventRepository._row_to_event(
            self._row(resolved_etoro_market=None)
        )

        self.assertIsNone(missing.resolved_etoro_market)
        self.assertIsNone(explicit_null.resolved_etoro_market)


if __name__ == "__main__":
    unittest.main()
