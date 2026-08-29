from __future__ import annotations

import unittest
from datetime import UTC, datetime

from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceState,
)
from trading_system.tracked_event_release_source import (
    build_tracked_event_release_source_read_model,
)
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


def _event(*, calendar_event_id: str | None = None) -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="11111111-1111-1111-1111-111111111111",
        tracked_instrument_id="22222222-2222-2222-2222-222222222222",
        calendar_event_id=calendar_event_id,
        company_name="Example Oyj",
        instrument="EXAMPLE.HE",
        market="Helsinki",
        source="manual",
        external_key="example-q2",
        kind="earnings",
        title="Q2 results",
        event_at=datetime(2026, 8, 29, 6, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.TRACKED,
    )


class TrackedEventReleaseSourceReadModelTests(unittest.TestCase):
    def test_generic_tracked_event_uses_canonical_tracked_release_identity(self) -> None:
        event = _event()
        source = OfficialReleaseSource(
            event_id=f"tracked:{event.event_id}",
            source_kind="direct_url",
            source_url="https://example.com/results.pdf",
            source_title="Q2 results",
            version=3,
        )

        model = build_tracked_event_release_source_read_model(
            event,
            OfficialReleaseSourceState(source=source, version=3),
        )

        self.assertEqual(model.event_id, event.event_id)
        self.assertEqual(model.release_event_id, f"tracked:{event.event_id}")
        self.assertTrue(model.active)
        self.assertEqual(model.version, 3)
        self.assertEqual(model.source_url, "https://example.com/results.pdf")

    def test_calendar_backed_tracked_event_uses_calendar_release_identity(self) -> None:
        calendar_event_id = "33333333-3333-3333-3333-333333333333"
        event = _event(calendar_event_id=calendar_event_id)

        model = build_tracked_event_release_source_read_model(
            event,
            OfficialReleaseSourceState(source=None, version=0),
        )

        self.assertEqual(model.release_event_id, f"calendar:{calendar_event_id}")
        self.assertFalse(model.active)
        self.assertIsNone(model.source_url)

    def test_active_source_must_match_canonical_release_identity(self) -> None:
        event = _event()
        source = OfficialReleaseSource(
            event_id="tracked:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            source_kind="results_page",
            source_url="https://example.com/investors",
            version=1,
        )

        with self.assertRaisesRegex(ValueError, "identity does not match"):
            build_tracked_event_release_source_read_model(
                event,
                OfficialReleaseSourceState(source=source, version=1),
            )

    def test_active_source_version_must_match_state_version(self) -> None:
        event = _event()
        source = OfficialReleaseSource(
            event_id=f"tracked:{event.event_id}",
            source_kind="results_page",
            source_url="https://example.com/investors",
            version=2,
        )

        with self.assertRaisesRegex(ValueError, "version does not match"):
            build_tracked_event_release_source_read_model(
                event,
                OfficialReleaseSourceState(source=source, version=3),
            )


if __name__ == "__main__":
    unittest.main()
