from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from trading_system.tracked_event_repository import SupabaseTrackedEventRepository


class _ReactionQuery:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.select_value: str | None = None
        self.filters: list[tuple[str, str]] = []
        self.orders: list[str] = []

    def select(self, value: str):
        self.select_value = value
        return self

    def eq(self, name: str, value: str):
        self.filters.append((name, value))
        return self

    def order(self, name: str, **_kwargs):
        self.orders.append(name)
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _ReactionClient:
    def __init__(self, rows) -> None:
        self.query = _ReactionQuery(rows)
        self.table_names: list[str] = []

    def table(self, name: str):
        self.table_names.append(name)
        return self.query


class TrackedEventReactionPrecisionTests(unittest.TestCase):
    def test_list_reactions_preserves_postgres_numeric_precision_as_text(self) -> None:
        event_id = "2346c3f3-f321-4b83-b3f4-bdbdb9f417d1"
        exact_return = "1.377657981431566337226714585"
        client = _ReactionClient(
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
        self.assertIsNotNone(client.query.select_value)
        select_value = client.query.select_value or ""
        self.assertIn("reference_price::text", select_value)
        self.assertIn("close_price::text", select_value)
        self.assertIn("return_pct::text", select_value)
        self.assertEqual(client.query.filters, [("tracked_market_event_id", event_id)])
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

    def test_wds_return_would_not_survive_a_float_round_trip(self) -> None:
        exact_return = Decimal("1.377657981431566337226714585")
        rounded_through_json_float = Decimal(str(float(str(exact_return))))

        self.assertNotEqual(rounded_through_json_float, exact_return)


if __name__ == "__main__":
    unittest.main()
