from __future__ import annotations

import asyncio
import unittest
from contextlib import ExitStack
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


def _run(event, *, repository, provider=None):
    return _prepare_and_monitor_one_event(
        event,
        repository=repository,
        provider=provider or _Provider(),
        monitor_hours=8.0,
        reference_lead_seconds=30.0,
        max_wait_for_market_hours=72.0,
    )


class _Patches:
    """Patch every worker collaborator so each test opts in to real behavior."""

    def __init__(self, stack: ExitStack, **overrides) -> None:
        def install(name, **kwargs):
            return stack.enter_context(
                patch(f"trading_system.tracked_event_worker.{name}", **kwargs)
            )

        self.acquire = install(
            "acquire_and_persist_pre_event_market_context_for_event",
            **overrides.get("acquire", {}),
        )
        self.is_current = install(
            "persisted_pre_event_market_context_is_current",
            **overrides.get("is_current", {"return_value": True}),
        )
        self.validate = install(
            "validate_pre_event_market_context_if_current",
            **overrides.get("validate", {}),
        )
        self.fail_deadline = install(
            "fail_pre_event_deadline_if_current",
            **overrides.get("fail_deadline", {}),
        )
        self.monitor = install("monitor_one_event", new=AsyncMock())


class TrackedEventWorkerPreEventContextTests(unittest.TestCase):
    def test_prepares_context_off_loop_before_monitoring(self) -> None:
        event = _event(event_at=datetime.now(UTC) + timedelta(hours=4))
        prepared = replace(event, updated_at=event.updated_at + timedelta(seconds=1))
        repository = _Repository()
        provider = _Provider()

        with ExitStack() as stack:
            p = _Patches(stack, acquire={"return_value": prepared})
            asyncio.run(_run(event, repository=repository, provider=provider))

        p.acquire.assert_called_once_with(
            repository,
            event_id=event.event_id,
            ticker="WDS.ASX",
            actor=WORKER_ACTOR,
        )
        p.monitor.assert_awaited_once_with(
            prepared,
            repository=repository,
            provider=provider,
            monitor_hours=8.0,
            reference_lead_seconds=30.0,
            max_wait_for_market_hours=72.0,
        )
        p.fail_deadline.assert_not_called()
        self.assertEqual(repository.failed, [])

    def test_preparation_failure_remains_retryable_and_does_not_monitor(self) -> None:
        # A fresh read at failure time still shows a future event_at, so the
        # original transient error must propagate unchanged (retryable).
        event = _event(event_at=datetime.now(UTC) + timedelta(hours=4))
        repository = _Repository(current_event=event)

        with ExitStack() as stack:
            p = _Patches(
                stack, acquire={"side_effect": RuntimeError("temporary Yahoo failure")}
            )
            with self.assertRaisesRegex(RuntimeError, "temporary Yahoo failure"):
                asyncio.run(_run(event, repository=repository))

        p.monitor.assert_not_awaited()
        p.fail_deadline.assert_not_called()
        self.assertEqual(repository.failed, [])

    def test_acquisition_failure_at_or_after_fresh_deadline_fails_closed(self) -> None:
        # Acquisition started while event.event_at (the object this coroutine
        # was called with) was still in the future, so the pre-check passed.
        # By the time it raised, a fresh read shows event_at has since been
        # reached - the failure must terminal-fail the event, bound to the
        # freshly-read row's own version.
        stale_event = _event(event_at=datetime.now(UTC) + timedelta(hours=4))
        current_event = replace(
            stale_event,
            event_at=datetime.now(UTC) - timedelta(seconds=1),
            updated_at=stale_event.updated_at + timedelta(seconds=5),
        )
        repository = _Repository(current_event=current_event)

        with ExitStack() as stack:
            p = _Patches(
                stack,
                acquire={"side_effect": RuntimeError("DB deadline gate rejected the capture")},
            )
            asyncio.run(_run(stale_event, repository=repository))

        p.monitor.assert_not_awaited()
        p.fail_deadline.assert_called_once_with(
            repository,
            event_id=current_event.event_id,
            expected_event_updated_at=current_event.updated_at,
            actor=WORKER_ACTOR,
            error=unittest.mock.ANY,
        )
        self.assertIn(
            "pre-event market context acquisition",
            p.fail_deadline.call_args.kwargs["error"],
        )

    def test_slow_acquisition_crossing_deadline_cannot_leave_event_stuck_tracked(self) -> None:
        # A long-blocking acquisition call can raise any exception type once
        # event_at has been crossed mid-call, not just RuntimeError - the
        # deadline handling must still terminal-fail rather than let an unusual
        # error type keep retrying past max_past with the event stuck TRACKED.
        stale_event = _event(event_at=datetime.now(UTC) + timedelta(hours=4))
        current_event = replace(stale_event, event_at=datetime.now(UTC) - timedelta(seconds=1))
        repository = _Repository(current_event=current_event)

        with ExitStack() as stack:
            p = _Patches(
                stack,
                acquire={
                    "side_effect": ValueError("confirmed closed session history is incomplete")
                },
            )
            asyncio.run(_run(stale_event, repository=repository))

        p.monitor.assert_not_awaited()
        p.fail_deadline.assert_called_once()

    def test_acquisition_error_with_concurrent_reschedule_stays_retryable(self) -> None:
        # Acquisition failed and the freshly-read row's event_at looks passed,
        # but the row changed again before the terminal write: the CAS RPC
        # refuses, and that must surface as a retryable error rather than a
        # terminal failure written on a version that no longer exists.
        stale_event = _event(event_at=datetime.now(UTC) + timedelta(hours=4))
        current_event = replace(stale_event, event_at=datetime.now(UTC) - timedelta(seconds=1))
        repository = _Repository(current_event=current_event)

        with ExitStack() as stack:
            p = _Patches(
                stack,
                acquire={"side_effect": RuntimeError("temporary Yahoo failure")},
                fail_deadline={
                    "side_effect": RuntimeError(
                        "tracked event changed before its pre-event deadline failure "
                        "could be recorded"
                    )
                },
            )
            with self.assertRaisesRegex(RuntimeError, "changed before its pre-event deadline"):
                asyncio.run(_run(stale_event, repository=repository))

        p.monitor.assert_not_awaited()
        p.fail_deadline.assert_called_once()
        self.assertEqual(repository.failed, [])

    def test_event_at_without_preparation_fails_closed_before_market_monitor(self) -> None:
        event = _event(event_at=datetime.now(UTC) - timedelta(seconds=1))
        repository = _Repository()

        with ExitStack() as stack:
            p = _Patches(stack)
            asyncio.run(_run(event, repository=repository))

        p.acquire.assert_not_called()
        p.monitor.assert_not_awaited()
        # Bound to the version the decision was made from, so a row that moved
        # on cannot be terminal-failed on this stale read.
        p.fail_deadline.assert_called_once_with(
            repository,
            event_id=event.event_id,
            expected_event_updated_at=event.updated_at,
            actor=WORKER_ACTOR,
            error=unittest.mock.ANY,
        )
        self.assertIn(
            "before pre-event market context was prepared",
            p.fail_deadline.call_args.kwargs["error"],
        )

    def test_stale_deadline_does_not_fail_an_event_rescheduled_in_the_db(self) -> None:
        # The in-memory event from list_runnable says the deadline passed, but
        # the DB row was rescheduled into the future. The CAS RPC rejects the
        # write, so the event must stay TRACKED and retryable - never
        # terminal-failed on the stale event_at.
        stale_event = _event(event_at=datetime.now(UTC) - timedelta(seconds=1))
        repository = _Repository(
            current_event=replace(stale_event, event_at=datetime.now(UTC) + timedelta(hours=6))
        )

        with ExitStack() as stack:
            p = _Patches(
                stack,
                fail_deadline={
                    "side_effect": RuntimeError(
                        "tracked event was rescheduled past its pre-event deadline "
                        "before the failure could be recorded"
                    )
                },
            )
            with self.assertRaisesRegex(RuntimeError, "rescheduled past its pre-event deadline"):
                asyncio.run(_run(stale_event, repository=repository))

        p.acquire.assert_not_called()
        p.monitor.assert_not_awaited()
        p.fail_deadline.assert_called_once()
        self.assertEqual(repository.failed, [])

    def test_tracked_restart_with_current_persisted_context_skips_reacquisition(self) -> None:
        # The stale object handed in mirrors what list_runnable() returned; the
        # repository's fresh read (current_event) is what revalidation and the
        # final CAS confirmation must actually operate on and hand to monitor.
        stale_event = _event(
            event_at=datetime.now(UTC) + timedelta(hours=4),
            pre_event_market_context=_SNAPSHOT,
        )
        current_event = replace(stale_event)
        confirmed_event = replace(
            current_event, updated_at=current_event.updated_at + timedelta(seconds=1)
        )
        repository = _Repository(current_event=current_event)
        provider = _Provider()

        with ExitStack() as stack:
            p = _Patches(
                stack,
                is_current={"return_value": True},
                validate={"return_value": confirmed_event},
            )
            asyncio.run(_run(stale_event, repository=repository, provider=provider))

        p.acquire.assert_not_called()
        self.assertEqual(repository.get_calls, [stale_event.event_id])
        p.is_current.assert_called_once_with(current_event)
        p.validate.assert_called_once_with(
            repository,
            event_id=current_event.event_id,
            expected_event_updated_at=current_event.updated_at,
        )
        p.monitor.assert_awaited_once_with(
            confirmed_event,
            repository=repository,
            provider=provider,
            monitor_hours=8.0,
            reference_lead_seconds=30.0,
            max_wait_for_market_hours=72.0,
        )
        p.fail_deadline.assert_not_called()
        self.assertEqual(repository.failed, [])

    def test_event_at_changed_since_stale_read_blocks_monitoring_on_old_validation(self) -> None:
        # The worker was handed a stale in-memory event. Revalidation must run
        # against the repository's fresh row, and a version conflict at the
        # final atomic confirmation must block monitoring rather than let it
        # proceed on the earlier (now-stale) validation.
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

        with ExitStack() as stack:
            p = _Patches(
                stack,
                is_current={"return_value": True},
                validate={
                    "side_effect": RuntimeError(
                        "tracked event changed before pre-event context revalidation completed"
                    )
                },
            )
            with self.assertRaisesRegex(RuntimeError, "changed before pre-event context revalidation"):
                asyncio.run(_run(stale_event, repository=repository))

        p.acquire.assert_not_called()
        p.is_current.assert_called_once_with(current_event)
        p.monitor.assert_not_awaited()
        # A version-conflict race is retryable, not a proven-invalid snapshot.
        self.assertEqual(repository.failed, [])
        p.fail_deadline.assert_not_called()

    def test_tracked_restart_with_stale_persisted_context_fails_closed_without_monitoring(
        self,
    ) -> None:
        # event_at moved to a different trading date after the context snapshot
        # was captured for the original event_at. This is a proven-invalid
        # snapshot rather than a deadline decision, so it stays on mark_failed.
        current_event = _event(
            event_at=datetime.now(UTC) + timedelta(hours=4),
            pre_event_market_context=_SNAPSHOT,
        )
        repository = _Repository(current_event=current_event)

        with ExitStack() as stack:
            p = _Patches(stack, is_current={"return_value": False})
            asyncio.run(_run(current_event, repository=repository))

        p.acquire.assert_not_called()
        p.is_current.assert_called_once_with(current_event)
        p.validate.assert_not_called()
        p.monitor.assert_not_awaited()
        self.assertEqual(len(repository.failed), 1)
        self.assertEqual(repository.failed[0][1], WORKER_ACTOR)
        self.assertIn("pre-event market context", repository.failed[0][2])

    def test_revalidation_error_before_deadline_stays_retryable(self) -> None:
        current_event = _event(
            event_at=datetime.now(UTC) + timedelta(hours=4),
            pre_event_market_context=_SNAPSHOT,
        )
        repository = _Repository(current_event=current_event)

        with ExitStack() as stack:
            p = _Patches(
                stack,
                is_current={"side_effect": RuntimeError("transient calendar loader failure")},
            )
            with self.assertRaisesRegex(RuntimeError, "transient calendar loader failure"):
                asyncio.run(_run(current_event, repository=repository))

        p.acquire.assert_not_called()
        p.validate.assert_not_called()
        p.monitor.assert_not_awaited()
        p.fail_deadline.assert_not_called()
        self.assertEqual(repository.failed, [])

    def test_revalidation_error_at_or_after_deadline_fails_closed_without_monitoring(self) -> None:
        current_event = _event(
            event_at=datetime.now(UTC) - timedelta(seconds=1),
            pre_event_market_context=_SNAPSHOT,
        )
        repository = _Repository(current_event=current_event)

        with ExitStack() as stack:
            p = _Patches(
                stack,
                is_current={"side_effect": RuntimeError("transient calendar loader failure")},
            )
            asyncio.run(_run(current_event, repository=repository))

        p.acquire.assert_not_called()
        p.validate.assert_not_called()
        p.monitor.assert_not_awaited()
        p.fail_deadline.assert_called_once_with(
            repository,
            event_id=current_event.event_id,
            expected_event_updated_at=current_event.updated_at,
            actor=WORKER_ACTOR,
            error=unittest.mock.ANY,
        )
        self.assertIn(
            "revalidation failed at or after event_at",
            p.fail_deadline.call_args.kwargs["error"],
        )

    def test_monitoring_restart_preserves_existing_legacy_path(self) -> None:
        event = _event(
            event_at=datetime.now(UTC) - timedelta(minutes=10),
            status=TrackedEventStatus.MONITORING,
        )
        repository = _Repository()

        with ExitStack() as stack:
            p = _Patches(stack)
            asyncio.run(_run(event, repository=repository))

        p.acquire.assert_not_called()
        p.monitor.assert_awaited_once()
        p.fail_deadline.assert_not_called()
        self.assertEqual(repository.failed, [])


if __name__ == "__main__":
    unittest.main()
