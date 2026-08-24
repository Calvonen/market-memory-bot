from __future__ import annotations

from datetime import UTC, datetime
import unittest

from trading_system.pre_event_market_context_persistence import (
    capture_pre_event_market_context,
    validate_pre_event_market_context_if_current,
)


class _RpcCall:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return object()


class _Client:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return _RpcCall(error=self.error)


class _Repository:
    def __init__(self, *, event=object(), error: Exception | None = None) -> None:
        self.client = _Client(error=error)
        self.event = event
        self.get_calls = []

    def get(self, event_id):
        self.get_calls.append(event_id)
        return self.event


class PreEventMarketContextPersistenceTests(unittest.TestCase):
    def test_calls_canonical_rpc_with_exact_payload_and_rereads_event(self) -> None:
        saved_event = object()
        repository = _Repository(event=saved_event)
        snapshot = {
            "schema_version": 1,
            "session_date": "2026-08-21",
            "previous_session_date": "2026-08-20",
        }

        result = capture_pre_event_market_context(
            repository,
            event_id="event-1",
            snapshot=snapshot,
            market_timezone="Australia/Sydney",
            actor="tracked-event-worker",
        )

        self.assertIs(result, saved_event)
        self.assertEqual(repository.get_calls, ["event-1"])
        self.assertEqual(
            repository.client.calls,
            [
                (
                    "capture_tracked_market_event_pre_event_context",
                    {
                        "input_event_id": "event-1",
                        "input_pre_event_market_context": snapshot,
                        "input_market_timezone": "Australia/Sydney",
                        "input_actor": "tracked-event-worker",
                    },
                )
            ],
        )

    def test_version_gate_uses_cas_rpc_with_exact_timestamp(self) -> None:
        saved_event = object()
        repository = _Repository(event=saved_event)
        version = datetime(2026, 8, 24, 14, 47, tzinfo=UTC)

        result = capture_pre_event_market_context(
            repository,
            event_id="event-1",
            snapshot={"schema_version": 1},
            market_timezone="Australia/Sydney",
            actor="tracked-event-worker",
            expected_event_updated_at=version,
        )

        self.assertIs(result, saved_event)
        rpc_name, payload = repository.client.calls[0]
        self.assertEqual(
            rpc_name,
            "capture_tracked_market_event_pre_event_context_if_current",
        )
        self.assertEqual(payload["input_expected_updated_at"], version.isoformat())

    def test_version_conflict_is_non_retryable_runtime_error(self) -> None:
        repository = _Repository(error=Exception("tracked_market_event_version_conflict"))

        with self.assertRaisesRegex(RuntimeError, "changed before pre-event context capture"):
            capture_pre_event_market_context(
                repository,
                event_id="event-1",
                snapshot={"schema_version": 1},
                market_timezone="Australia/Sydney",
                actor="tracked-event-worker",
                expected_event_updated_at=datetime(2026, 8, 24, 14, 47, tzinfo=UTC),
            )

        self.assertEqual(repository.get_calls, [])

    def test_different_existing_snapshot_is_non_retryable_runtime_error(self) -> None:
        repository = _Repository(
            error=Exception("tracked_market_event_pre_event_context_locked")
        )

        with self.assertRaisesRegex(RuntimeError, "different pre_event_market_context"):
            capture_pre_event_market_context(
                repository,
                event_id="event-1",
                snapshot={"schema_version": 1},
                market_timezone="Europe/London",
                actor="tracked-event-worker",
            )

        self.assertEqual(repository.get_calls, [])

    def test_missing_event_after_successful_capture_fails_closed(self) -> None:
        repository = _Repository(event=None)

        with self.assertRaisesRegex(RuntimeError, "could not be re-read"):
            capture_pre_event_market_context(
                repository,
                event_id="event-1",
                snapshot={"schema_version": 1},
                market_timezone="Europe/London",
                actor="tracked-event-worker",
            )


class ValidatePreEventMarketContextIfCurrentTests(unittest.TestCase):
    def test_calls_cas_rpc_with_exact_timestamp_and_rereads_event(self) -> None:
        confirmed_event = object()
        repository = _Repository(event=confirmed_event)
        version = datetime(2026, 8, 24, 14, 47, tzinfo=UTC)

        result = validate_pre_event_market_context_if_current(
            repository,
            event_id="event-1",
            expected_event_updated_at=version,
        )

        self.assertIs(result, confirmed_event)
        self.assertEqual(repository.get_calls, ["event-1"])
        self.assertEqual(
            repository.client.calls,
            [
                (
                    "validate_tracked_market_event_pre_event_context_if_current",
                    {
                        "input_event_id": "event-1",
                        "input_expected_updated_at": version.isoformat(),
                    },
                )
            ],
        )

    def test_rejects_timezone_naive_expected_updated_at(self) -> None:
        repository = _Repository(event=object())

        with self.assertRaisesRegex(ValueError, "must be timezone-aware"):
            validate_pre_event_market_context_if_current(
                repository,
                event_id="event-1",
                expected_event_updated_at=datetime(2026, 8, 24, 14, 47),
            )

        self.assertEqual(repository.client.calls, [])

    def test_version_conflict_is_retryable_runtime_error(self) -> None:
        repository = _Repository(error=Exception("tracked_market_event_version_conflict"))

        with self.assertRaisesRegex(RuntimeError, "changed before pre-event context revalidation"):
            validate_pre_event_market_context_if_current(
                repository,
                event_id="event-1",
                expected_event_updated_at=datetime(2026, 8, 24, 14, 47, tzinfo=UTC),
            )

        self.assertEqual(repository.get_calls, [])

    def test_missing_event_after_successful_validation_fails_closed(self) -> None:
        repository = _Repository(event=None)

        with self.assertRaisesRegex(RuntimeError, "could not be re-read"):
            validate_pre_event_market_context_if_current(
                repository,
                event_id="event-1",
                expected_event_updated_at=datetime(2026, 8, 24, 14, 47, tzinfo=UTC),
            )


if __name__ == "__main__":
    unittest.main()
