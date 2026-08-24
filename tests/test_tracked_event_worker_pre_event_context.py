from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)
from trading_system.tracked_event_worker import (
    WORKER_ACTOR,
    _prepare_and_monitor_one_event,
)


class _Repository:
    def __init__(self) -> None:
        self.failed: list[tuple[str, str, str]] = []

    def mark_failed(self, event_id, *, actor, error):
        self.failed.append((event_id, actor, error))


class _Provider:
    pass


def _event(*, event_at: datetime, status: TrackedEventStatus = TrackedEventStatus.TRACKED):
    return PersistentTrackedEvent(
        event_id="11111111-1111-1111-1111-111111111111",
        tracked_instrument_id="tracked-wds",
        calendar_event_id=None,
        company_name="Woodside Energy Group Ltd",
        instrument="WDS.ASX",
        market="Australia",
        source="manual_ir",
        external_key="wds-hy26-2026-08-25",
        kind="earnings",
        title="Woodside HY26 Half-Year Results",
        event_at=event_at,
        event_time_status=TrackedEventTimeStatus.ESTIMATED,
        status=status,
        resolved_etoro_instrument_id=7016,
        resolved_etoro_symbol="WDS.ASX",
        resolved_etoro_display_name="Woodside Energy Group Ltd",
        resolved_etoro_market="Sydney",
        resolution_armed_at=event_at - timedelta(hours=4),
        resolution_armed_by="tracked-event-preflight",
        created_by="test",
        updated_by="test",
        created_at=event_at - timedelta(days=1),
        updated_at=event_at - timedelta(hours=3),
    )


class TrackedEventWorkerPreEventContextTests(unittest.TestCase):
    def test_prepares_context_off_loop_before_monitoring(self) -> None:
        event = _event(event_at=datetime.now(UTC) + timedelta(hours=4))
        prepared = replace(event, updated_at=event.updated_at + timedelta(seconds=1))
        repository = _Repository()
        provider = _Provider()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event",
            return_value=prepared,
        ) as acquire, patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            asyncio.run(
                _prepare_and_monitor_one_event(
                    event,
                    repository=repository,
                    provider=provider,
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                )
            )

        acquire.assert_called_once_with(
            repository,
            event_id=event.event_id,
            ticker="WDS.ASX",
            actor=WORKER_ACTOR,
        )
        monitor.assert_awaited_once_with(
            prepared,
            repository=repository,
            provider=provider,
            monitor_hours=8.0,
            reference_lead_seconds=30.0,
            max_wait_for_market_hours=72.0,
        )
        self.assertEqual(repository.failed, [])

    def test_preparation_failure_remains_retryable_and_does_not_monitor(self) -> None:
        event = _event(event_at=datetime.now(UTC) + timedelta(hours=4))
        repository = _Repository()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event",
            side_effect=RuntimeError("temporary Yahoo failure"),
        ), patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            with self.assertRaisesRegex(RuntimeError, "temporary Yahoo failure"):
                asyncio.run(
                    _prepare_and_monitor_one_event(
                        event,
                        repository=repository,
                        provider=_Provider(),
                        monitor_hours=8.0,
                        reference_lead_seconds=30.0,
                        max_wait_for_market_hours=72.0,
                    )
                )

        monitor.assert_not_awaited()
        self.assertEqual(repository.failed, [])

    def test_event_at_without_preparation_fails_closed_before_market_monitor(self) -> None:
        event = _event(event_at=datetime.now(UTC) - timedelta(seconds=1))
        repository = _Repository()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event"
        ) as acquire, patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            asyncio.run(
                _prepare_and_monitor_one_event(
                    event,
                    repository=repository,
                    provider=_Provider(),
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                )
            )

        acquire.assert_not_called()
        monitor.assert_not_awaited()
        self.assertEqual(len(repository.failed), 1)
        self.assertEqual(repository.failed[0][1], WORKER_ACTOR)
        self.assertIn("pre-event market context", repository.failed[0][2])

    def test_tracked_restart_with_current_persisted_context_skips_reacquisition(self) -> None:
        event = replace(
            _event(event_at=datetime.now(UTC) + timedelta(hours=4)),
            pre_event_market_context={
                "schema_version": 1,
                "session_date": "2026-08-21",
                "previous_session_date": "2026-08-20",
            },
        )
        repository = _Repository()
        provider = _Provider()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event"
        ) as acquire, patch(
            "trading_system.tracked_event_worker.persisted_pre_event_market_context_is_current",
            return_value=True,
        ) as is_current, patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            asyncio.run(
                _prepare_and_monitor_one_event(
                    event,
                    repository=repository,
                    provider=provider,
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                )
            )

        acquire.assert_not_called()
        is_current.assert_called_once_with(event)
        monitor.assert_awaited_once_with(
            event,
            repository=repository,
            provider=provider,
            monitor_hours=8.0,
            reference_lead_seconds=30.0,
            max_wait_for_market_hours=72.0,
        )
        self.assertEqual(repository.failed, [])

    def test_tracked_restart_with_stale_persisted_context_fails_closed_without_monitoring(
        self,
    ) -> None:
        # event_at moved to a different trading date after the context snapshot
        # was captured for the original event_at (see upsert_tracked_market_event,
        # which still allows editing event_at while TRACKED with no reference).
        event = replace(
            _event(event_at=datetime.now(UTC) + timedelta(hours=4)),
            pre_event_market_context={
                "schema_version": 1,
                "session_date": "2026-08-21",
                "previous_session_date": "2026-08-20",
            },
        )
        repository = _Repository()
        provider = _Provider()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event"
        ) as acquire, patch(
            "trading_system.tracked_event_worker.persisted_pre_event_market_context_is_current",
            return_value=False,
        ) as is_current, patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            asyncio.run(
                _prepare_and_monitor_one_event(
                    event,
                    repository=repository,
                    provider=provider,
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                )
            )

        acquire.assert_not_called()
        is_current.assert_called_once_with(event)
        monitor.assert_not_awaited()
        self.assertEqual(len(repository.failed), 1)
        self.assertEqual(repository.failed[0][1], WORKER_ACTOR)
        self.assertIn("pre-event market context", repository.failed[0][2])

    def test_monitoring_restart_preserves_existing_legacy_path(self) -> None:
        event = _event(
            event_at=datetime.now(UTC) - timedelta(minutes=10),
            status=TrackedEventStatus.MONITORING,
        )
        repository = _Repository()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event"
        ) as acquire, patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            asyncio.run(
                _prepare_and_monitor_one_event(
                    event,
                    repository=repository,
                    provider=_Provider(),
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                )
            )

        acquire.assert_not_called()
        monitor.assert_awaited_once()
        self.assertEqual(repository.failed, [])


if __name__ == "__main__":
    unittest.main()
