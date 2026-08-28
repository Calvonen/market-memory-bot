from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace

from trading_system.canonical_tracked_event_ingress import SupabaseCanonicalTrackedEventIngress
from trading_system.manual_market_event_ingress import persist_manual_market_event
from trading_system.market_event import MarketEventKind
from trading_system.tracked_event_repository import TrackedEventTimeStatus
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


TRACKED = TrackedEtoroInstrument(
    tracked_instrument_id="tracked-hvn",
    instrument="HVN.ASX",
    market="Australia",
    etoro_instrument_id=3326,
    etoro_symbol="HVN.ASX",
    etoro_display_name="Harvey Norman Holdings Limited",
)


class _RpcCall:
    def __init__(self, data) -> None:
        self.data = data

    def execute(self):
        return SimpleNamespace(data=self.data)


class _FakeClient:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, params: dict):
        self.calls.append((name, params))
        return _RpcCall(self.rows)


class ManualCanonicalTrackedEventIngressTests(unittest.TestCase):
    def test_manual_hvn_uses_explicit_australian_event_date(self) -> None:
        client = _FakeClient(
            [
                {
                    "out_id": "event-hvn",
                    "out_tracked_instrument_id": "tracked-hvn",
                    "out_event_date": "2026-08-28",
                    "out_action": "inserted",
                }
            ]
        )
        result = persist_manual_market_event(
            SupabaseCanonicalTrackedEventIngress(client),
            TRACKED,
            company_name="Harvey Norman Holdings Limited",
            external_key="hvn-fy26-2026-08-28",
            event_at=datetime(2026, 8, 27, 23, 15, tzinfo=UTC),
            event_date=date(2026, 8, 28),
            event_time_status=TrackedEventTimeStatus.ESTIMATED,
            actor="test",
            kind=MarketEventKind.EARNINGS,
            title="Harvey Norman FY26 results",
        )

        self.assertEqual(result.event_date, date(2026, 8, 28))
        name, params = client.calls[0]
        self.assertEqual(name, "upsert_canonical_tracked_market_event")
        self.assertEqual(params["input_instrument"], "HVN.ASX")
        self.assertEqual(params["input_market"], "Australia")
        self.assertEqual(params["input_source"], "manual")
        self.assertEqual(params["input_event_at"], "2026-08-27T23:15:00+00:00")
        self.assertEqual(params["input_event_date"], "2026-08-28")
        self.assertIsNone(params["input_calendar_event_id"])

    def test_fails_closed_if_database_binds_other_tracked_instrument(self) -> None:
        client = _FakeClient(
            [
                {
                    "out_id": "event-hvn",
                    "out_tracked_instrument_id": "other-instrument",
                    "out_event_date": "2026-08-28",
                    "out_action": "inserted",
                }
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "different tracked instrument"):
            persist_manual_market_event(
                SupabaseCanonicalTrackedEventIngress(client),
                TRACKED,
                company_name="Harvey Norman Holdings Limited",
                external_key="hvn-fy26-2026-08-28",
                event_at=datetime(2026, 8, 27, 23, 15, tzinfo=UTC),
                event_date=date(2026, 8, 28),
                event_time_status=TrackedEventTimeStatus.ESTIMATED,
                actor="test",
                kind=MarketEventKind.EARNINGS,
            )


if __name__ == "__main__":
    unittest.main()
