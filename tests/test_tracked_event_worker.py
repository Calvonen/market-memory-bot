from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from trading_system.etoro_market_data import EtoroInstrumentCandidate, EtoroQuote
from trading_system.reaction_monitoring_profile import DEFAULT_EVENT_REACTION_MONITORING_PROFILE
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_event_config import snapshot_effective_tracking_config
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventReactionRecord,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)
from trading_system.tracked_event_worker import (
    DEFAULT_MAX_WAIT_FOR_MARKET_HOURS,
    DEFAULT_REFERENCE_LEAD_SECONDS,
    _capture_reference_if_needed,
    _preflight_resolution_sync,
    _resolved_identity_from_event,
    _tracked_identity,
    monitor_one_event,
)


class _FakeProvider:
    def __init__(self, *, quote_timestamp: datetime | None = None, instrument_id: int = 777) -> None:
        self.quote_timestamp = quote_timestamp
        self.instrument_id = instrument_id
        self.search_calls: list[str] = []
        self.quote_calls: list[int] = []

    def search_instruments(self, term: str):
        self.search_calls.append(term)
        return (
            EtoroInstrumentCandidate(
                instrument_id=self.instrument_id,
                symbol="NHF.ASX",
                display_name="nib holdings limited",
                market="Sydney",
            ),
        )

    def fetch_quote(self, instrument_id: int) -> EtoroQuote:
        self.quote_calls.append(instrument_id)
        return EtoroQuote(
            instrument_id=instrument_id,
            bid=Decimal("7.49"),
            ask=Decimal("7.51"),
            last_execution=Decimal("7.50"),
            timestamp=self.quote_timestamp,
        )


class _FakeRepository:
    def __init__(
        self,
        event: PersistentTrackedEvent | None = None,
        *,
        snapshot_capture_error: Exception | None = None,
    ) -> None:
        self.event = event
        self.captured = None
        self.armed = []
        self.anchors = []
        self.monitoring = []
        self.completed = []
        self.failed = []
        self.reactions: list[TrackedEventReactionRecord] = []
        self.snapshot_capture_error = snapshot_capture_error
        self.snapshot_captures: list[tuple[str, dict, str]] = []
        self.call_order: list[str] = []

    def capture_tracking_config_snapshot(self, *, event_id, snapshot, actor):
        if self.event is None:
            raise AssertionError("test repository needs an event")
        self.call_order.append("snapshot")
        self.snapshot_captures.append((event_id, snapshot, actor))
        if self.snapshot_capture_error is not None:
            raise self.snapshot_capture_error
        existing = self.event.tracking_config_snapshot
        if existing is not None and existing != snapshot:
            raise RuntimeError(
                f"tracked event {event_id} already has a different tracking_config_snapshot"
            )
        self.event = replace(self.event, tracking_config_snapshot=snapshot)
        return self.event

    def arm_resolution(self, *, event_id, etoro_instrument_id, etoro_symbol, etoro_display_name, actor):
        if self.event is None:
            raise AssertionError("test repository needs an event")
        self.armed.append((event_id, etoro_instrument_id, etoro_symbol, etoro_display_name, actor))
        self.event = replace(
            self.event,
            resolved_etoro_instrument_id=etoro_instrument_id,
            resolved_etoro_symbol=etoro_symbol,
            resolved_etoro_display_name=etoro_display_name,
            resolution_armed_at=datetime.now(UTC),
            resolution_armed_by=actor,
        )
        return self.event

    def capture_reference(self, **kwargs):
        if self.event is None:
            raise AssertionError("test repository needs an event")
        self.captured = kwargs
        self.event = replace(
            self.event,
            reference_price=kwargs["reference_price"],
            reference_captured_at=kwargs["captured_at"],
            reference_kind=kwargs["reference_kind"],
        )
        return self.event

    def capture_reaction_anchor(self, *, event_id, reaction_anchor_at, actor):
        if self.event is None:
            raise AssertionError("test repository needs an event")
        self.anchors.append((event_id, reaction_anchor_at, actor))
        self.event = replace(self.event, reaction_anchor_at=reaction_anchor_at)
        return self.event

    def mark_monitoring(self, event_id, *, actor, started_at):
        self.call_order.append("monitoring")
        self.monitoring.append((event_id, actor, started_at))

    def mark_completed(self, event_id, *, actor, completed_at):
        self.completed.append((event_id, actor, completed_at))

    def mark_failed(self, event_id, *, actor, error):
        self.failed.append((event_id, actor, error))

    def list_reactions(self, event_id):
        return tuple(
            reaction
            for reaction in self.reactions
            if reaction.tracked_market_event_id == event_id
        )

    def save_reaction(self, record):
        self.reactions.append(record)


def _event(
    *,
    event_at: datetime,
    reference_price: Decimal | None = None,
    armed: bool | None = None,
) -> PersistentTrackedEvent:
    if armed is None:
        armed = reference_price is not None
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
        event_at=event_at,
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.TRACKED,
        resolved_etoro_instrument_id=777 if armed else None,
        resolved_etoro_symbol="NHF.ASX" if armed else None,
        resolved_etoro_display_name="nib holdings limited" if armed else None,
        resolution_armed_at=(event_at - timedelta(hours=2)) if armed else None,
        resolution_armed_by="test-preflight" if armed else None,
        reference_price=reference_price,
        reference_captured_at=(event_at - timedelta(hours=1)) if reference_price is not None else None,
        reference_kind=(
            "etoro_last_execution_pre_event_snapshot" if reference_price is not None else None
        ),
        created_by="test",
        updated_by="test",
    )


class TrackedEventWorkerTests(unittest.TestCase):
    def test_tracked_identity_preserves_persistent_id(self):
        event = _event(event_at=datetime.now(UTC) + timedelta(hours=1))
        tracked = _tracked_identity(event)
        self.assertEqual(tracked.tracked_instrument_id, "tracked-nhf")
        self.assertEqual(tracked.instrument, "NHF.ASX")

    def test_preflight_resolves_validates_quote_and_persists_identity(self):
        event = _event(event_at=datetime.now(UTC) + timedelta(hours=2))
        repo = _FakeRepository(event)
        provider = _FakeProvider()

        armed = _preflight_resolution_sync(event, repository=repo, provider=provider)

        self.assertEqual(armed.resolved_etoro_instrument_id, 777)
        self.assertEqual(armed.resolved_etoro_symbol, "NHF.ASX")
        self.assertTrue(provider.search_calls)
        self.assertEqual(provider.quote_calls, [777])
        self.assertEqual(len(repo.armed), 1)

    def test_monitor_uses_persisted_identity_without_catalog_search(self):
        event_at = datetime.now(UTC) - timedelta(minutes=5)
        event = _event(event_at=event_at, reference_price=Decimal("7.50"), armed=True)
        event = replace(event, reaction_anchor_at=event_at)
        repo = _FakeRepository(event)
        provider = _FakeProvider()
        candle_start = datetime.now(UTC) - timedelta(minutes=1)
        candle = TrackedMarketCandle(
            tracked_instrument_id=event.tracked_instrument_id,
            instrument="NHF.ASX",
            market="Sydney",
            etoro_instrument_id=777,
            interval_minutes=1,
            start=candle_start,
            open=Decimal("7.50"),
            high=Decimal("7.62"),
            low=Decimal("7.49"),
            close=Decimal("7.60"),
            source_minutes=1,
        )

        async def fake_stream(*args, **kwargs):
            yield SimpleNamespace(candles=(candle,))

        with patch(
            "trading_system.tracked_event_worker.stream_tracked_event_reaction_runtime",
            side_effect=lambda *args, **kwargs: fake_stream(),
        ):
            import asyncio

            asyncio.run(
                monitor_one_event(
                    event,
                    repository=repo,
                    provider=provider,
                    monitor_hours=1.0,
                )
            )

        self.assertEqual(provider.search_calls, [])
        self.assertEqual(len(repo.reactions), 1)
        self.assertEqual(repo.failed, [])

    def test_unarmed_monitor_fails_closed_without_catalog_search(self):
        event = _event(event_at=datetime.now(UTC) - timedelta(minutes=1), armed=False)
        repo = _FakeRepository(event)
        provider = _FakeProvider()

        import asyncio

        asyncio.run(monitor_one_event(event, repository=repo, provider=provider))

        self.assertEqual(provider.search_calls, [])
        self.assertEqual(len(repo.failed), 1)
        self.assertIn("not armed", repo.failed[0][2])

    def test_resolved_identity_requires_complete_arming_metadata(self):
        event = replace(
            _event(event_at=datetime.now(UTC) + timedelta(hours=1), armed=True),
            resolution_armed_at=None,
        )
        with self.assertRaisesRegex(RuntimeError, "not armed"):
            _resolved_identity_from_event(event)

    def test_reference_capture_uses_pre_event_etoro_last_execution(self):
        event_at = datetime.now(UTC) + timedelta(hours=2)
        quote_at = event_at - timedelta(hours=8)
        event = _event(event_at=event_at, armed=True)
        repo = _FakeRepository(event)
        provider = _FakeProvider(quote_timestamp=quote_at)
        resolved = _resolved_identity_from_event(event)

        saved = _capture_reference_if_needed(
            event,
            repository=repo,
            provider=provider,
            resolved=resolved,
            now=event_at - timedelta(hours=1),
        )

        self.assertEqual(saved.reference_price, Decimal("7.50"))
        self.assertEqual(saved.reference_captured_at, quote_at)
        self.assertEqual(repo.captured["reference_kind"], "etoro_last_execution_pre_event_snapshot")

    def test_missing_reference_after_event_fails_closed(self):
        event_at = datetime.now(UTC) - timedelta(minutes=1)
        event = _event(event_at=event_at, armed=True)
        repo = _FakeRepository(event)
        provider = _FakeProvider(quote_timestamp=event_at - timedelta(hours=1))
        resolved = _resolved_identity_from_event(event)
        with self.assertRaisesRegex(RuntimeError, "without a persisted pre-event reference"):
            _capture_reference_if_needed(
                event,
                repository=repo,
                provider=provider,
                resolved=resolved,
                now=datetime.now(UTC),
            )

    def test_first_tradable_candle_anchors_profile_even_hours_after_event(self):
        event_at = datetime.now(UTC) - timedelta(hours=2)
        event = _event(event_at=event_at, reference_price=Decimal("7.50"), armed=True)
        repo = _FakeRepository(event)
        provider = _FakeProvider()
        candle_start = datetime.now(UTC) - timedelta(minutes=1)
        candle = TrackedMarketCandle(
            tracked_instrument_id=event.tracked_instrument_id,
            instrument="NHF.ASX",
            market="Sydney",
            etoro_instrument_id=777,
            interval_minutes=1,
            start=candle_start,
            open=Decimal("7.50"),
            high=Decimal("7.62"),
            low=Decimal("7.49"),
            close=Decimal("7.60"),
            source_minutes=1,
        )

        async def fake_stream(*args, **kwargs):
            yield SimpleNamespace(candles=(candle,))

        with patch(
            "trading_system.tracked_event_worker.stream_tracked_event_reaction_runtime",
            side_effect=lambda *args, **kwargs: fake_stream(),
        ):
            import asyncio

            asyncio.run(
                monitor_one_event(
                    event,
                    repository=repo,
                    provider=provider,
                    monitor_hours=1.0,
                )
            )

        self.assertEqual(len(repo.anchors), 1)
        self.assertEqual(repo.anchors[0][1], candle_start)
        self.assertEqual(len(repo.reactions), 1)
        self.assertEqual(repo.reactions[0].interval_minutes, 1)
        self.assertEqual(repo.reactions[0].return_pct, Decimal("1.333333333333333333333333333"))
        self.assertEqual(len(repo.completed), 1)
        self.assertEqual(repo.failed, [])

    def test_restart_restores_evolution_before_new_candle(self):
        now = datetime.now(UTC)
        event_at = now - timedelta(minutes=5)
        anchor_at = event_at
        event = replace(
            _event(event_at=event_at, reference_price=Decimal("100"), armed=True),
            status=TrackedEventStatus.MONITORING,
            reaction_anchor_at=anchor_at,
        )
        repo = _FakeRepository(event)
        repo.reactions.append(
            TrackedEventReactionRecord(
                tracked_market_event_id=event.event_id,
                interval_minutes=1,
                candle_start=event_at + timedelta(minutes=1),
                reference_price=Decimal("100"),
                close_price=Decimal("101"),
                return_pct=Decimal("1.00"),
                direction="positive",
                evolution="initial",
                observed_at=event_at + timedelta(minutes=2),
            )
        )
        provider = _FakeProvider()
        candle = TrackedMarketCandle(
            tracked_instrument_id=event.tracked_instrument_id,
            instrument="NHF.ASX",
            market="Sydney",
            etoro_instrument_id=777,
            interval_minutes=1,
            start=event_at + timedelta(minutes=2),
            open=Decimal("102"),
            high=Decimal("102"),
            low=Decimal("102"),
            close=Decimal("102"),
            source_minutes=1,
        )

        async def fake_stream(*args, **kwargs):
            yield SimpleNamespace(candles=(candle,))

        with patch(
            "trading_system.tracked_event_worker.stream_tracked_event_reaction_runtime",
            side_effect=lambda *args, **kwargs: fake_stream(),
        ):
            import asyncio

            asyncio.run(
                monitor_one_event(
                    event,
                    repository=repo,
                    provider=provider,
                    monitor_hours=1.0,
                )
            )

        self.assertEqual(len(repo.reactions), 2)
        self.assertEqual(repo.reactions[-1].evolution, "extending")
        self.assertEqual(repo.snapshot_captures, [])
        self.assertIsNone(repo.event.tracking_config_snapshot)
        self.assertEqual(repo.failed, [])

    def _expected_snapshot(self, *, monitor_hours: float) -> dict:
        return snapshot_effective_tracking_config(
            monitor_hours=monitor_hours,
            reference_lead_seconds=DEFAULT_REFERENCE_LEAD_SECONDS,
            max_wait_for_market_hours=DEFAULT_MAX_WAIT_FOR_MARKET_HOURS,
            profile=DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
        ).to_dict()

    def test_snapshot_capture_happens_before_monitoring_begins(self):
        event_at = datetime.now(UTC) - timedelta(hours=2)
        event = _event(event_at=event_at, reference_price=Decimal("7.50"), armed=True)
        repo = _FakeRepository(event)
        provider = _FakeProvider()
        candle_start = datetime.now(UTC) - timedelta(minutes=1)
        candle = TrackedMarketCandle(
            tracked_instrument_id=event.tracked_instrument_id,
            instrument="NHF.ASX",
            market="Sydney",
            etoro_instrument_id=777,
            interval_minutes=1,
            start=candle_start,
            open=Decimal("7.50"),
            high=Decimal("7.62"),
            low=Decimal("7.49"),
            close=Decimal("7.60"),
            source_minutes=1,
        )

        async def fake_stream(*args, **kwargs):
            yield SimpleNamespace(candles=(candle,))

        with patch(
            "trading_system.tracked_event_worker.stream_tracked_event_reaction_runtime",
            side_effect=lambda *args, **kwargs: fake_stream(),
        ):
            import asyncio

            asyncio.run(
                monitor_one_event(
                    event,
                    repository=repo,
                    provider=provider,
                    monitor_hours=1.0,
                )
            )

        self.assertEqual(len(repo.snapshot_captures), 1)
        captured_event_id, captured_snapshot, captured_actor = repo.snapshot_captures[0]
        self.assertEqual(captured_event_id, event.event_id)
        self.assertEqual(captured_snapshot, self._expected_snapshot(monitor_hours=1.0))
        self.assertEqual(captured_actor, "tracked-event-worker")
        self.assertEqual(repo.event.tracking_config_snapshot, captured_snapshot)
        self.assertIn("snapshot", repo.call_order)
        self.assertIn("monitoring", repo.call_order)
        self.assertLess(repo.call_order.index("snapshot"), repo.call_order.index("monitoring"))
        self.assertEqual(repo.failed, [])

    def test_snapshot_capture_failure_fails_closed_before_monitoring(self):
        event_at = datetime.now(UTC) + timedelta(hours=1)
        event = _event(event_at=event_at, armed=True)
        repo = _FakeRepository(
            event,
            snapshot_capture_error=RuntimeError(
                f"tracked event {event.event_id} already has a different tracking_config_snapshot"
            ),
        )
        provider = _FakeProvider()

        import asyncio

        asyncio.run(monitor_one_event(event, repository=repo, provider=provider))

        self.assertEqual(len(repo.snapshot_captures), 1)
        self.assertEqual(repo.monitoring, [])
        self.assertEqual(repo.armed, [])
        self.assertEqual(repo.reactions, [])
        self.assertEqual(repo.completed, [])
        self.assertEqual(len(repo.failed), 1)
        self.assertIn("different tracking_config_snapshot", repo.failed[0][2])
        self.assertEqual(provider.quote_calls, [])

    def test_conflicting_existing_snapshot_fails_closed_without_overwrite(self):
        event_at = datetime.now(UTC) + timedelta(hours=1)
        event = replace(
            _event(event_at=event_at, armed=True),
            tracking_config_snapshot=self._expected_snapshot(monitor_hours=99.0),
        )
        repo = _FakeRepository(event)
        provider = _FakeProvider()

        import asyncio

        asyncio.run(monitor_one_event(event, repository=repo, provider=provider, monitor_hours=1.0))

        self.assertEqual(len(repo.snapshot_captures), 1)
        self.assertEqual(repo.monitoring, [])
        self.assertEqual(len(repo.failed), 1)
        self.assertIn("different tracking_config_snapshot", repo.failed[0][2])
        # Never overwritten with the newly attempted (different) content.
        self.assertEqual(repo.event.tracking_config_snapshot, self._expected_snapshot(monitor_hours=99.0))

    def test_existing_identical_snapshot_lets_restart_continue(self):
        now = datetime.now(UTC)
        event_at = now - timedelta(minutes=5)
        expected_snapshot = self._expected_snapshot(monitor_hours=1.0)
        event = replace(
            _event(event_at=event_at, reference_price=Decimal("100"), armed=True),
            status=TrackedEventStatus.MONITORING,
            reaction_anchor_at=event_at,
            tracking_config_snapshot=expected_snapshot,
        )
        repo = _FakeRepository(event)
        provider = _FakeProvider()
        candle = TrackedMarketCandle(
            tracked_instrument_id=event.tracked_instrument_id,
            instrument="NHF.ASX",
            market="Sydney",
            etoro_instrument_id=777,
            interval_minutes=1,
            start=event_at + timedelta(minutes=1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("100"),
            close=Decimal("101"),
            source_minutes=1,
        )

        async def fake_stream(*args, **kwargs):
            yield SimpleNamespace(candles=(candle,))

        with patch(
            "trading_system.tracked_event_worker.stream_tracked_event_reaction_runtime",
            side_effect=lambda *args, **kwargs: fake_stream(),
        ):
            import asyncio

            asyncio.run(
                monitor_one_event(
                    event,
                    repository=repo,
                    provider=provider,
                    monitor_hours=1.0,
                )
            )

        self.assertEqual(len(repo.snapshot_captures), 1)
        self.assertEqual(repo.event.tracking_config_snapshot, expected_snapshot)
        self.assertEqual(len(repo.reactions), 1)
        self.assertEqual(repo.failed, [])


if __name__ == "__main__":
    unittest.main()