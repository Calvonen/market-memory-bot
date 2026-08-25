from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from trading_system.tracked_event_repository import SupabaseTrackedEventRepository


class _Query:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.select_value: str | None = None
        self.filters: list[tuple[str, object]] = []
        self.orders: list[str] = []

    def select(self, value: str):
        self.select_value = value
        return self

    def eq(self, name: str, value: object):
        self.filters.append((f"eq:{name}", value))
        return self

    def in_(self, name: str, value: object):
        self.filters.append((f"in:{name}", value))
        return self

    def lte(self, name: str, value: object):
        self.filters.append((f"lte:{name}", value))
        return self

    def or_(self, value: str):
        self.filters.append(("or", value))
        return self

    def order(self, name: str, **_kwargs):
        self.orders.append(name)
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Client:
    def __init__(self, rows) -> None:
        self.query = _Query(rows)
        self.table_names: list[str] = []

    def table(self, name: str):
        self.table_names.append(name)
        return self.query


class TrackedEventReactionPrecisionTests(unittest.TestCase):
    def test_list_reactions_preserves_postgres_numeric_precision_as_text(self) -> None:
        event_id = "2346c3f3-f321-4b83-b3f4-bdbdb9f417d1"
        exact_return = "1.377657981431566337226714585"
        client = _Client(
            [
                {
                    "tracked_market_event_id": event_id,
                    "interval_minutes": 1,
                    "candle_start": "2026-08-25T00:00:00+00:00",
                    "reference_price": "33.39",
                    "close_price": "33.85",
                    "return_pct": exact_return,
                    "direction": "positive",
                    "evolution": "initial",
                    "observed_at": "2026-08-25T00:01:00+00:00",
                }
            ]
        )
        repository = SupabaseTrackedEventRepository(client)

        reactions = repository.list_reactions(event_id)

        self.assertEqual(client.table_names, ["tracked_market_event_reactions"])
        select_value = client.query.select_value or ""
        self.assertIn("reference_price::text", select_value)
        self.assertIn("close_price::text", select_value)
        self.assertIn("return_pct::text", select_value)
        self.assertEqual(client.query.orders, ["candle_start", "interval_minutes"])
        self.assertEqual(len(reactions), 1)
        self.assertEqual(reactions[0].reference_price, Decimal("33.39"))
        self.assertEqual(reactions[0].close_price, Decimal("33.85"))
        self.assertEqual(reactions[0].return_pct, Decimal(exact_return))
        self.assertEqual(
            ((reactions[0].close_price - reactions[0].reference_price)
             / reactions[0].reference_price)
            * Decimal("100"),
            reactions[0].return_pct,
        )

    def test_list_runnable_prefers_exact_text_reference_for_restart_baseline(self) -> None:
        exact_reference = "1.234567890123456789"
        rounded_reference = float(exact_reference)
        event_at = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
        client = _Client(
            [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "tracked_instrument_id": "tracked-precision",
                    "calendar_event_id": None,
                    "company_name": "Precision Ltd",
                    "instrument": "PREC.ASX",
                    "market": "Australia",
                    "source": "manual",
                    "external_key": "precision-2026-08-25",
                    "kind": "earnings",
                    "title": "Results",
                    "event_at": event_at.isoformat(),
                    "event_time_status": "confirmed",
                    "status": "monitoring",
                    "resolved_etoro_instrument_id": 777,
                    "resolved_etoro_symbol": "PREC.ASX",
                    "resolved_etoro_display_name": "Precision Ltd",
                    "resolved_etoro_market": "Sydney",
                    "resolution_armed_at": (event_at - timedelta(hours=1)).isoformat(),
                    "resolution_armed_by": "test",
                    "reference_price": rounded_reference,
                    "reference_price_exact": exact_reference,
                    "reference_captured_at": (event_at - timedelta(seconds=30)).isoformat(),
                    "reference_kind": "etoro_last_execution_pre_event_snapshot",
                    "reaction_anchor_at": event_at.isoformat(),
                    "started_at": event_at.isoformat(),
                    "completed_at": None,
                    "last_error": None,
                    "created_by": "test",
                    "updated_by": "test",
                    "created_at": (event_at - timedelta(hours=2)).isoformat(),
                    "updated_at": event_at.isoformat(),
                    "tracking_config_snapshot": {"schema_version": 1},
                    "pre_event_market_context": None,
                }
            ]
        )
        repository = SupabaseTrackedEventRepository(client)

        events = repository.list_runnable(
            now=event_at + timedelta(minutes=5),
            lookahead=timedelta(hours=24),
            max_past=timedelta(hours=12),
        )

        self.assertIn(
            "reference_price_exact:reference_price::text",
            client.query.select_value or "",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].reference_price, Decimal(exact_reference))
        self.assertNotEqual(events[0].reference_price, Decimal(str(rounded_reference)))

    def test_wds_return_would_not_survive_a_float_round_trip(self) -> None:
        exact_return = Decimal("1.377657981431566337226714585")
        rounded_through_json_float = Decimal(str(float(str(exact_return))))

        self.assertNotEqual(rounded_through_json_float, exact_return)


if __name__ == "__main__":
    unittest.main()
