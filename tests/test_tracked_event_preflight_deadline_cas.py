from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    SupabaseTrackedEventRepository,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)
from trading_system.tracked_event_worker import run_forever


class _StopLoop(Exception):
    pass


class _RpcCall:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=[])


class _Client:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return _RpcCall(self.error)


class _Repository:
    def __init__(
        self,
        event: PersistentTrackedEvent,
        *,
        rpc_error: Exception | None = None,
    ) -> None:
        self.event = event
        self.list_calls = 0
        self.client = _Client(rpc_error)

    def list_runnable(self, *, now, lookahead, max_past):
        self.list_calls += 1
        return (self.event,)


def _past_unarmed_event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="11111111-1111-1111-1111-111111111111",
        tracked_instrument_id="tracked-wds",
        calendar_event_id=None,
        company_name="Woodside Energy Group Ltd",
        instrument="WDS.ASX",
        market="Australia",
        source="manual_ir",
        external_key="wds-hy26",
        kind="earnings",
        title="Woodside HY26",
        event_at=datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.ESTIMATED,
        status=TrackedEventStatus.TRACKED,
        updated_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    )


def _past_armed_unsupported_event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id="22222222-2222-2222-2222-222222222222",
        tracked_instrument_id="tracked-unsupported",
        calendar_event_id=None,
        company_name="Unsupported Market Ltd",
        instrument="XYZ.TEST",
        market="Test",
        source="manual_ir",
        external_key="unsupported-results",
        kind="earnings",
        title="Unsupported results",
        event_at=datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.ESTIMATED,
        status=TrackedEventStatus.TRACKED,
        updated_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
        resolved_etoro_instrument_id=12345,
        resolved_etoro_symbol="XYZ.TEST",
        resolved_etoro_display_name="Unsupported Market Ltd",
        resolved_etoro_market="Unsupported Exchange",
        resolution_armed_at=datetime(2026, 8, 23, 11, 0, tzinfo=UTC),
        resolution_armed_by="tracked-event-preflight",
    )


class TrackedEventPreflightDeadlineCasTests(unittest.TestCase):
    def _run_one_poll(self, repository, *, patch_deadline=False):
        async def stop_after_first_poll(_seconds):
            raise _StopLoop

        async def inline_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        stack = [
            patch.object(SupabaseTrackedEventRepository, "from_env", return_value=repository),
            patch.object(EtoroMarketDataProvider, "from_env", return_value=object()),
            patch(
                "trading_system.tracked_event_worker.asyncio.to_thread",
                new=AsyncMock(side_effect=inline_to_thread),
            ),
            patch(
                "trading_system.tracked_event_worker.asyncio.sleep",
                new=AsyncMock(side_effect=stop_after_first_poll),
            ),
        ]
        if patch_deadline:
            stack.append(
                patch("trading_system.tracked_event_worker._fail_pre_event_deadline")
            )

        entered = [manager.__enter__() for manager in stack]
        try:
            with self.assertRaises(_StopLoop):
                asyncio.run(run_forever())
            return entered
        finally:
            for manager in reversed(stack):
                manager.__exit__(None, None, None)

    def test_past_unarmed_event_uses_version_bound_deadline_helper_off_loop(self) -> None:
        event = _past_unarmed_event()
        repository = _Repository(event)

        entered = self._run_one_poll(repository, patch_deadline=True)
        to_thread = entered[2]
        fail_deadline = entered[4]

        fail_deadline.assert_called_once_with(
            repository,
            event=event,
            error="event reached event_at before the pre-event lifecycle completed",
        )
        self.assertIs(to_thread.await_args.args[0], fail_deadline)
        self.assertEqual(repository.list_calls, 1)

    def test_past_armed_unsupported_market_still_uses_deadline_cas(self) -> None:
        event = _past_armed_unsupported_event()
        repository = _Repository(event)

        entered = self._run_one_poll(repository, patch_deadline=True)
        fail_deadline = entered[4]

        fail_deadline.assert_called_once_with(
            repository,
            event=event,
            error="event reached event_at before the pre-event lifecycle completed",
        )
        self.assertEqual(repository.list_calls, 1)

    def test_version_conflict_does_not_terminate_worker_loop(self) -> None:
        event = _past_unarmed_event()
        repository = _Repository(
            event,
            rpc_error=Exception("tracked_market_event_version_conflict"),
        )

        self._run_one_poll(repository)

        self.assertEqual(repository.list_calls, 1)
        self.assertEqual(
            repository.client.calls[0][0],
            "fail_tracked_market_event_pre_event_deadline_if_current",
        )


if __name__ == "__main__":
    unittest.main()
