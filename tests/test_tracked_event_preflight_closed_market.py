from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.etoro_market_data import EtoroInstrumentCandidate, EtoroQuote
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)
from trading_system.tracked_event_worker import _preflight_resolution_sync


class _Provider:
    def search_instruments(self, term: str):
        return (
            EtoroInstrumentCandidate(
                instrument_id=777,
                symbol="NHF.ASX",
                display_name="nib holdings limited",
                market="Sydney",
            ),
        )

    def fetch_quote(self, instrument_id: int) -> EtoroQuote:
        return EtoroQuote(
            instrument_id=instrument_id,
            bid=Decimal("0"),
            ask=Decimal("0"),
            last_execution=Decimal("0"),
            timestamp=None,
        )


class _Repository:
    def __init__(self, event: PersistentTrackedEvent) -> None:
        self.event = event
        self.calls = []

    def arm_resolution(self, **kwargs):
        self.calls.append(kwargs)
        self.event = replace(
            self.event,
            resolved_etoro_instrument_id=kwargs["etoro_instrument_id"],
            resolved_etoro_symbol=kwargs["etoro_symbol"],
            resolved_etoro_display_name=kwargs["etoro_display_name"],
            resolution_armed_at=datetime.now(UTC),
            resolution_armed_by=kwargs["actor"],
        )
        return self.event


def _event() -> PersistentTrackedEvent:
    now = datetime.now(UTC)
    return PersistentTrackedEvent(
        event_id="11111111-1111-1111-1111-111111111111",
        tracked_instrument_id="tracked-nhf",
        calendar_event_id=None,
        company_name="nib holdings limited",
        instrument="NHF.ASX",
        market="Sydney",
        source="manual",
        external_key="nhf-fy26-2026-08-24",
        kind="earnings",
        title="FY26 results",
        event_at=now + timedelta(hours=2),
        event_time_status=TrackedEventTimeStatus.ESTIMATED,
        status=TrackedEventStatus.TRACKED,
        created_by="test",
        updated_by="test",
    )


class ClosedMarketPreflightTests(unittest.TestCase):
    def test_zero_last_execution_does_not_block_identity_arming(self):
        event = _event()
        repository = _Repository(event)

        armed = _preflight_resolution_sync(
            event,
            repository=repository,
            provider=_Provider(),
        )

        self.assertEqual(armed.resolved_etoro_instrument_id, 777)
        self.assertEqual(armed.resolved_etoro_symbol, "NHF.ASX")
        self.assertEqual(len(repository.calls), 1)


if __name__ == "__main__":
    unittest.main()
