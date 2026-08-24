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
    def __init__(self, *, current_event: PersistentTrackedEvent | None = None) -> None:
        self.failed: list[tuple[str, str, str]] = []
        self.current_event = current_event
        self.get_calls: list[str] = []

    def mark_failed(self, event_id, *, actor, error):
        self.failed.append((event_id, actor, error))

    def get(self, event_id):
        self.get_calls.append(event_id)
        return self.current_event


class _Provider:
    pass


def _event(
    *,
    event_at: datetime,
    status: TrackedEventStatus = TrackedEventStatus.TRACKED,
    updated_at: datetime | None = None,
    pre_event_market_context: dict[str, object] | None = None,
):
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
        pre_event_market_context=pre_event_market_context,
        created_by="test",
        updated_by="test",
        created_at=event_at - timedelta(days=1),
        updated_at=updated_at or event_at - timedelta(hours=3),
    )


_SNAPSHOT = {
    "schema_version": 1,
    "session_date": "2026-08-21",
    "previous_session_date": "2026-08-20",
}


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
        # A fresh read at failure time still shows a future event_at, so the
        # original transient error must propagate unchanged (retryable).
        event = _event(event_at=datetime.now(UTC) + timedelta(hours=4))
        repository = _Repository(current_event=event)

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

    def test_acquisition_failure_at_or_after_fresh_deadline_fails_closed(self) -> None:
        # Acquisition started while event.event_at (the object this coroutine
        # was called with) was still in the future, so the pre-check passed.
        # By the time it raised, a fresh read shows event_at has since been
        # reached (elapsed real time, or a concurrent edit) - the failure must
        # terminal-fail the event instead of leaving it retryable forever.
        stale_event = _event(event_at=datetime.now(UTC) + timedelta(hours=4))
        current_event = replace(stale_event, event_at=datetime.now(UTC) - timedelta(seconds=1))
        repository = _Repository(current_event=current_event)

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event",
            side_effect=RuntimeError("DB deadline gate rejected the capture"),
        ), patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            asyncio.run(
                _prepare_and_monitor_one_event(
                    stale_event,
                    repository=repository,
                    provider=_Provider(),
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                )
            )

        monitor.assert_not_awaited()
        self.assertEqual(len(repository.failed), 1)
        self.assertEqual(repository.failed[0][0], stale_event.event_id)
        self.assertEqual(repository.failed[0][1], WORKER_ACTOR)
        self.assertIn("pre-event market context acquisition", repository.failed[0][2])

    def test_slow_acquisition_crossing_deadline_cannot_leave_event_stuck_tracked(self) -> None:
        # A long-blocking acquisition call (e.g. a slow Yahoo fetch) can raise
        # any exception type once event_at has been crossed mid-call, not just
        # RuntimeError - the deadline handling must still terminal-fail the
        # event rather than let an unusual error type keep retrying past
        # max_past with the event stuck in TRACKED.
        stale_event = _event(event_at=datetime.now(UTC) + timedelta(hours=4))
        current_event = replace(stale_event, event_at=datetime.now(UTC) - timedelta(seconds=1))
        repository = _Repository(current_event=current_event)

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event",
            side_effect=ValueError("confirmed closed session history is incomplete"),
        ), patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            asyncio.run(
                _prepare_and_monitor_one_event(
                    stale_event,
                    repository=repository,
                    provider=_Provider(),
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                )
            )

        monitor.assert_not_awaited()
        self.assertEqual(len(repository.failed), 1)
        self.assertEqual(repository.failed[0][1], WORKER_ACTOR)
        self.assertIn("pre-event market context acquisition", repository.failed[0][2])

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
        # The stale object handed in mirrors what list_runnable() returned; the
        # repository's fresh read (current_event) is what revalidation and the
        # final CAS confirmation must actually operate on and hand to monitor.
        stale_event = _event(
            event_at=datetime.now(UTC) + timedelta(hours=4),
            pre_event_market_context=_SNAPSHOT,
        )
        current_event = replace(stale_event)
        confirmed_event = replace(current_event, updated_at=current_event.updated_at + timedelta(seconds=1))
        repository = _Repository(current_event=current_event)
        provider = _Provider()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event"
        ) as acquire, patch(
            "trading_system.tracked_event_worker.persisted_pre_event_market_context_is_current",
            return_value=True,
        ) as is_current, patch(
            "trading_system.tracked_event_worker.validate_pre_event_market_context_if_current",
            return_value=confirmed_event,
        ) as validate, patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            asyncio.run(
                _prepare_and_monitor_one_event(
                    stale_event,
                    repository=repository,
                    provider=provider,
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                )
            )

        acquire.assert_not_called()
        self.assertEqual(repository.get_calls, [stale_event.event_id])
        is_current.assert_called_once_with(current_event)
        validate.assert_called_once_with(
            repository,
            event_id=current_event.event_id,
            expected_event_updated_at=current_event.updated_at,
        )
        monitor.assert_awaited_once_with(
            confirmed_event,
            repository=repository,
            provider=provider,
            monitor_hours=8.0,
            reference_lead_seconds=30.0,
            max_wait_for_market_hours=72.0,
        )
        self.assertEqual(repository.failed, [])

    def test_event_at_changed_since_stale_read_blocks_monitoring_on_old_validation(self) -> None:
        # The worker was handed a stale in-memory event (as list_runnable would
        # return before a concurrent event_at edit landed). Revalidation must
        # run against the repository's fresh row, and a version conflict at the
        # final atomic confirmation - simulating event_at changing again in the
        # gap between that fresh read and this decision - must block monitoring
        # rather than let it proceed on the earlier (now-stale) validation.
        stale_event = _event(
            event_at=datetime.now(UTC) + timedelta(hours=4),
            pre_event_market_context=_SNAPSHOT,
        )
        current_event = replace(
            stale_event,
            event_at=stale_event.event_at + timedelta(hours=1),
            updated_at=stale_event.updated_at + timedelta(seconds=5),
        )
        repository = _Repository(current_event=current_event)
        provider = _Provider()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event"
        ) as acquire, patch(
            "trading_system.tracked_event_worker.persisted_pre_event_market_context_is_current",
            return_value=True,
        ) as is_current, patch(
            "trading_system.tracked_event_worker.validate_pre_event_market_context_if_current",
            side_effect=RuntimeError(
                f"tracked event {stale_event.event_id} changed before pre-event context revalidation completed"
            ),
        ) as validate, patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            with self.assertRaisesRegex(RuntimeError, "changed before pre-event context revalidation"):
                asyncio.run(
                    _prepare_and_monitor_one_event(
                        stale_event,
                        repository=repository,
                        provider=provider,
                        monitor_hours=8.0,
                        reference_lead_seconds=30.0,
                        max_wait_for_market_hours=72.0,
                    )
                )

        acquire.assert_not_called()
        # Revalidation used the fresh row's event_at, never the stale one.
        is_current.assert_called_once_with(current_event)
        validate.assert_called_once_with(
            repository,
            event_id=current_event.event_id,
            expected_event_updated_at=current_event.updated_at,
        )
        monitor.assert_not_awaited()
        # A version-conflict race is retryable, not a proven-invalid snapshot.
        self.assertEqual(repository.failed, [])

    def test_tracked_restart_with_stale_persisted_context_fails_closed_without_monitoring(
        self,
    ) -> None:
        # event_at moved to a different trading date after the context snapshot
        # was captured for the original event_at (see upsert_tracked_market_event,
        # which still allows editing event_at while TRACKED with no reference).
        current_event = _event(
            event_at=datetime.now(UTC) + timedelta(hours=4),
            pre_event_market_context=_SNAPSHOT,
        )
        repository = _Repository(current_event=current_event)
        provider = _Provider()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event"
        ) as acquire, patch(
            "trading_system.tracked_event_worker.persisted_pre_event_market_context_is_current",
            return_value=False,
        ) as is_current, patch(
            "trading_system.tracked_event_worker.validate_pre_event_market_context_if_current"
        ) as validate, patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            asyncio.run(
                _prepare_and_monitor_one_event(
                    current_event,
                    repository=repository,
                    provider=provider,
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                )
            )

        acquire.assert_not_called()
        is_current.assert_called_once_with(current_event)
        validate.assert_not_called()
        monitor.assert_not_awaited()
        self.assertEqual(len(repository.failed), 1)
        self.assertEqual(repository.failed[0][1], WORKER_ACTOR)
        self.assertIn("pre-event market context", repository.failed[0][2])

    def test_revalidation_error_before_deadline_stays_retryable(self) -> None:
        current_event = _event(
            event_at=datetime.now(UTC) + timedelta(hours=4),
            pre_event_market_context=_SNAPSHOT,
        )
        repository = _Repository(current_event=current_event)
        provider = _Provider()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event"
        ) as acquire, patch(
            "trading_system.tracked_event_worker.persisted_pre_event_market_context_is_current",
            side_effect=RuntimeError("transient calendar loader failure"),
        ), patch(
            "trading_system.tracked_event_worker.validate_pre_event_market_context_if_current"
        ) as validate, patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            with self.assertRaisesRegex(RuntimeError, "transient calendar loader failure"):
                asyncio.run(
                    _prepare_and_monitor_one_event(
                        current_event,
                        repository=repository,
                        provider=provider,
                        monitor_hours=8.0,
                        reference_lead_seconds=30.0,
                        max_wait_for_market_hours=72.0,
                    )
                )

        acquire.assert_not_called()
        validate.assert_not_called()
        monitor.assert_not_awaited()
        self.assertEqual(repository.failed, [])

    def test_revalidation_error_at_or_after_deadline_fails_closed_without_monitoring(self) -> None:
        current_event = _event(
            event_at=datetime.now(UTC) - timedelta(seconds=1),
            pre_event_market_context=_SNAPSHOT,
        )
        repository = _Repository(current_event=current_event)
        provider = _Provider()

        with patch(
            "trading_system.tracked_event_worker.acquire_and_persist_pre_event_market_context_for_event"
        ) as acquire, patch(
            "trading_system.tracked_event_worker.persisted_pre_event_market_context_is_current",
            side_effect=RuntimeError("transient calendar loader failure"),
        ), patch(
            "trading_system.tracked_event_worker.validate_pre_event_market_context_if_current"
        ) as validate, patch(
            "trading_system.tracked_event_worker.monitor_one_event",
            new=AsyncMock(),
        ) as monitor:
            asyncio.run(
                _prepare_and_monitor_one_event(
                    current_event,
                    repository=repository,
                    provider=provider,
                    monitor_hours=8.0,
                    reference_lead_seconds=30.0,
                    max_wait_for_market_hours=72.0,
                )
            )

        acquire.assert_not_called()
        validate.assert_not_called()
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
