from decimal import Decimal
from types import SimpleNamespace
import unittest

from trading_system.tracked_event_repository import TrackedEventStatus
from trading_system.tracked_event_worker import _needs_resolution_preflight


class TrackedEventMarketPreflightRolloutTests(unittest.TestCase):
    def test_unarmed_event_still_requires_preflight(self) -> None:
        event = SimpleNamespace(
            resolved_etoro_instrument_id=None,
            resolved_etoro_market=None,
            status=TrackedEventStatus.TRACKED,
            reference_price=None,
        )
        self.assertTrue(_needs_resolution_preflight(event))

    def test_new_armed_tracked_event_without_market_requires_backfill(self) -> None:
        event = SimpleNamespace(
            resolved_etoro_instrument_id=777,
            resolved_etoro_market=None,
            status=TrackedEventStatus.TRACKED,
            reference_price=None,
        )
        self.assertTrue(_needs_resolution_preflight(event))

    def test_legacy_tracked_event_with_reference_can_resume_without_market(self) -> None:
        event = SimpleNamespace(
            resolved_etoro_instrument_id=777,
            resolved_etoro_market=None,
            status=TrackedEventStatus.TRACKED,
            reference_price=Decimal("7.50"),
        )
        self.assertFalse(_needs_resolution_preflight(event))

    def test_legacy_monitoring_event_can_resume_without_market(self) -> None:
        event = SimpleNamespace(
            resolved_etoro_instrument_id=777,
            resolved_etoro_market=None,
            status=TrackedEventStatus.MONITORING,
            reference_price=Decimal("7.50"),
        )
        self.assertFalse(_needs_resolution_preflight(event))

    def test_persisted_market_never_requires_preflight(self) -> None:
        event = SimpleNamespace(
            resolved_etoro_instrument_id=777,
            resolved_etoro_market="Sydney",
            status=TrackedEventStatus.TRACKED,
            reference_price=None,
        )
        self.assertFalse(_needs_resolution_preflight(event))


if __name__ == "__main__":
    unittest.main()
