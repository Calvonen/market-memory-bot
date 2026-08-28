from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace

from trading_system.canonical_tracked_event_ingress import (
    SupabaseCanonicalTrackedEventIngress,
)
from trading_system.tracked_event_repository import TrackedEventTimeStatus


class _RpcCall:
    def __init__(self, response) -> None:
        self.response = response

    def execute(self):
        return self.response


class _FakeClient:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, params: dict):
        self.calls.append((name, params))
        return _RpcCall(SimpleNamespace(data=self.rows))


class CanonicalTrackedEventIngressTests(unittest.TestCase):
    def test_registers_explicit_local_date_through_canonical_rpc(self) -> None:
        client = _FakeClient(
            [
                {
                    "out_id": "event-1",
                    "out_tracked_instrument_id": "instrument-1",
                    "out_event_date": "2026-08-28",
                    "out_action": "inserted",
                }
            ]
        )
        ingress = SupabaseCanonicalTrackedEventIngress(client)

        result = ingress.register(
            company_name="Harvey Norman Holdings Limited",
            instrument="HVN.ASX",
            market="Australia",
            source="manual",
            external_key="hvn-fy26-2026-08-28",
            kind="earnings",
            title="Harvey Norman FY26 results",
            event_at=datetime(2026, 8, 27, 23, 15, tzinfo=UTC),
            event_date=date(2026, 8, 28),
            event_time_status=TrackedEventTimeStatus.ESTIMATED,
            actor="test",
        )

        self.assertEqual(result.event_date, date(2026, 8, 28))
        self.assertEqual(client.calls[0][0], "upsert_canonical_tracked_market_event")
        params = client.calls[0][1]
        self.assertEqual(params["input_event_at"], "2026-08-27T23:15:00+00:00")
        self.assertEqual(params["input_event_date"], "2026-08-28")
        self.assertEqual(params["input_event_time_status"], "estimated")
        self.assertIsNone(params["input_calendar_event_id"])

    def test_rejects_calendar_binding_before_rpc(self) -> None:
        client = _FakeClient([])
        ingress = SupabaseCanonicalTrackedEventIngress(client)
        with self.assertRaisesRegex(ValueError, "use calendar runtime promotion"):
            ingress.register(
                company_name="Autodesk",
                instrument="ADSK",
                market="USA",
                source="finnhub",
                external_key="calendar:22648076-6e43-40fc-ac6e-f57a79ceee31",
                kind="earnings",
                title="Autodesk earnings",
                event_at=datetime(2026, 8, 27, 20, tzinfo=UTC),
                event_date=date(2026, 8, 27),
                event_time_status=TrackedEventTimeStatus.ESTIMATED,
                actor="test",
                calendar_event_id="22648076-6e43-40fc-ac6e-f57a79ceee31",
            )
        self.assertEqual(client.calls, [])

    def test_rejects_datetime_as_event_date(self) -> None:
        ingress = SupabaseCanonicalTrackedEventIngress(_FakeClient([]))
        with self.assertRaisesRegex(ValueError, "event_date must be a date"):
            ingress.register(
                company_name="x",
                instrument="X",
                market="USA",
                source="scanner",
                external_key="x",
                kind="earnings",
                title="x",
                event_at=datetime(2026, 8, 28, 12, tzinfo=UTC),
                event_date=datetime(2026, 8, 28, 0, tzinfo=UTC),
                event_time_status=TrackedEventTimeStatus.CONFIRMED,
                actor="test",
            )

    def test_fails_if_database_returns_different_local_date(self) -> None:
        ingress = SupabaseCanonicalTrackedEventIngress(
            _FakeClient(
                [
                    {
                        "out_id": "event-1",
                        "out_tracked_instrument_id": "instrument-1",
                        "out_event_date": "2026-08-27",
                        "out_action": "noop_locked",
                    }
                ]
            )
        )
        with self.assertRaisesRegex(RuntimeError, "different event_date"):
            ingress.register(
                company_name="x",
                instrument="X",
                market="Australia",
                source="manual",
                external_key="x",
                kind="earnings",
                title="x",
                event_at=datetime(2026, 8, 27, 23, 15, tzinfo=UTC),
                event_date=date(2026, 8, 28),
                event_time_status=TrackedEventTimeStatus.ESTIMATED,
                actor="test",
            )

    def test_requires_timezone_aware_event_at(self) -> None:
        ingress = SupabaseCanonicalTrackedEventIngress(_FakeClient([]))
        with self.assertRaisesRegex(ValueError, "event_at must be timezone-aware"):
            ingress.register(
                company_name="x",
                instrument="X",
                market="USA",
                source="scanner",
                external_key="x",
                kind="earnings",
                title="x",
                event_at=datetime(2026, 8, 28, 12),
                event_date=date(2026, 8, 28),
                event_time_status=TrackedEventTimeStatus.UNKNOWN,
                actor="test",
            )


if __name__ == "__main__":
    unittest.main()
