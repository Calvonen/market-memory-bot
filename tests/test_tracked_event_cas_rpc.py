from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

from trading_system.tracked_event_cas import fail_tracked_event_if_current


class _RpcCall:
    def __init__(self, error=None):
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=[{"id": "event-1"}])


class _Client:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return _RpcCall(self.error)


class TrackedEventCasRpcTests(unittest.TestCase):
    def test_uses_canonical_version_bound_rpc(self) -> None:
        repository = SimpleNamespace(client=_Client())
        version = datetime(2026, 8, 25, 6, 0, tzinfo=UTC)

        fail_tracked_event_if_current(
            repository,
            event_id="event-1",
            expected_event_updated_at=version,
            actor="tracked-event-worker",
            error="stale context",
        )

        name, payload = repository.client.calls[0]
        self.assertEqual(name, "fail_tracked_market_event_stale_context_if_current")
        self.assertEqual(payload["input_expected_updated_at"], version.isoformat())
        self.assertEqual(payload["input_error"], "stale context")

    def test_version_or_progress_conflict_is_retryable(self) -> None:
        repository = SimpleNamespace(
            client=_Client(RuntimeError("tracked_market_event_version_conflict"))
        )
        with self.assertRaisesRegex(RuntimeError, "changed before"):
            fail_tracked_event_if_current(
                repository,
                event_id="event-1",
                expected_event_updated_at=datetime(2026, 8, 25, 6, 0, tzinfo=UTC),
                actor="tracked-event-worker",
                error="stale context",
            )


if __name__ == "__main__":
    unittest.main()
