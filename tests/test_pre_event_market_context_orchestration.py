from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from trading_system.pre_event_market_context_orchestration import (
    acquire_and_persist_pre_event_market_context,
    acquire_and_persist_pre_event_market_context_for_event,
    persisted_pre_event_market_context_is_current,
)


class _RpcCall:
    def __init__(self, repository) -> None:
        self.repository = repository

    def execute(self):
        return object()


class _Client:
    def __init__(self, repository) -> None:
        self.repository = repository
        self.calls = []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return _RpcCall(self.repository)


class _Repository:
    def __init__(self, event) -> None:
        self.saved_event = event
        self.client = _Client(self)
        self.get_calls = []

    def get(self, event_id):
        self.get_calls.append(event_id)
        return self.saved_event


class _Calendar:
    def __init__(self, closes: dict[str, datetime]) -> None:
        self._closes = closes
        self.sessions = pd.DatetimeIndex(closes.keys())

    def sessions_in_range(self, start, end):
        start = pd.Timestamp(start)
        end = pd.Timestamp(end)
        return self.sessions[(self.sessions >= start) & (self.sessions <= end)]

    def session_close(self, session):
        return pd.Timestamp(self._closes[session.strftime("%Y-%m-%d")])


def _daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [Decimal("10"), Decimal("11")],
            "High": [Decimal("11"), Decimal("13")],
            "Low": [Decimal("9"), Decimal("10")],
            "Close": [Decimal("10.5"), Decimal("12")],
            "Volume": [100, 200],
        },
        index=pd.DatetimeIndex(["2026-08-21", "2026-08-24"]),
    )


class PreEventMarketContextOrchestrationTests(unittest.TestCase):
    def test_caller_grounded_same_day_fails_before_provider_fetch(self) -> None:
        event = SimpleNamespace(
            instrument="WDS.ASX",
            event_at=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
        )
        repository = _Repository(event)
        fetch_calls = []

        with self.assertRaisesRegex(ValueError, "same-day capture requires the validated canonical path"):
            acquire_and_persist_pre_event_market_context(
                repository,
                event_id="event-1",
                ticker="WDS.ASX",
                provider_symbol="WDS.AX",
                event_trading_date=date(2026, 8, 24),
                last_confirmed_closed_session_date=date(2026, 8, 24),
                previous_confirmed_closed_session_date=date(2026, 8, 21),
                market_timezone="Australia/Sydney",
                actor="tracked-event-worker",
                fetcher=lambda ticker, period, interval: fetch_calls.append((ticker, period, interval)) or _daily_frame(),
            )

        self.assertEqual(fetch_calls, [])
        self.assertEqual(repository.client.calls, [])

    def test_caller_grounded_inconsistent_later_trading_date_still_fails_before_fetch(self) -> None:
        # The caller cannot make a same-day snapshot look like a prior-day one
        # simply by supplying a later event_trading_date. The persisted event_at
        # remains the authority, matching the base RPC's own event-local-date gate.
        event = SimpleNamespace(
            instrument="WDS.ASX",
            event_at=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
        )
        repository = _Repository(event)
        fetch_calls = []

        with self.assertRaisesRegex(ValueError, "persisted event local date"):
            acquire_and_persist_pre_event_market_context(
                repository,
                event_id="event-1",
                ticker="WDS.ASX",
                provider_symbol="WDS.AX",
                event_trading_date=date(2026, 8, 25),
                last_confirmed_closed_session_date=date(2026, 8, 24),
                previous_confirmed_closed_session_date=date(2026, 8, 21),
                market_timezone="Australia/Sydney",
                actor="tracked-event-worker",
                fetcher=lambda ticker, period, interval: fetch_calls.append((ticker, period, interval)) or _daily_frame(),
            )

        self.assertEqual(fetch_calls, [])
        self.assertEqual(repository.client.calls, [])

    def test_caller_grounded_prior_day_keeps_broker_and_provider_symbols_separate(self) -> None:
        event = SimpleNamespace(
            instrument="WDS.ASX",
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
        )
        repository = _Repository(event)
        fetch_calls = []

        acquire_and_persist_pre_event_market_context(
            repository,
            event_id="event-1",
            ticker="WDS.ASX",
            provider_symbol="WDS.AX",
            event_trading_date=date(2026, 8, 25),
            last_confirmed_closed_session_date=date(2026, 8, 24),
            previous_confirmed_closed_session_date=date(2026, 8, 21),
            market_timezone="Australia/Sydney",
            actor="tracked-event-worker",
            fetcher=lambda ticker, period, interval: fetch_calls.append((ticker, period, interval)) or _daily_frame(),
        )

        self.assertEqual(fetch_calls, [("WDS.AX", "1mo", "1d")])
        self.assertEqual(repository.client.calls[0][0], "capture_tracked_market_event_pre_event_context")

    def test_auto_entrypoint_uses_profile_provider_symbol_and_validated_rpc(self) -> None:
        version = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
        event = SimpleNamespace(
            instrument="WDS.ASX",
            resolved_etoro_market="Sydney",
            event_at=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            updated_at=version,
        )
        repository = _Repository(event)
        calendar = _Calendar(
            {
                "2026-08-20": datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
                "2026-08-21": datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
                "2026-08-24": datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
                "2026-08-25": datetime(2026, 8, 25, 6, 0, tzinfo=UTC),
            }
        )
        fetch_calls = []

        saved = acquire_and_persist_pre_event_market_context_for_event(
            repository,
            event_id="event-1",
            ticker="WDS.ASX",
            actor="tracked-event-worker",
            fetcher=lambda ticker, period, interval: fetch_calls.append((ticker, period, interval)) or _daily_frame(),
            calendar_loader=lambda calendar_id: calendar,
        )

        self.assertIs(saved, event)
        self.assertEqual(fetch_calls, [("WDS.AX", "1mo", "1d")])
        rpc_name, payload = repository.client.calls[0]
        self.assertEqual(rpc_name, "capture_tracked_market_event_pre_event_context_validated")
        self.assertEqual(payload["input_expected_updated_at"], version.isoformat())
        self.assertEqual(payload["input_session_close"], datetime(2026, 8, 24, 6, 0, tzinfo=UTC).isoformat())
        self.assertEqual(payload["input_pre_event_market_context"]["session_date"], "2026-08-24")

    def test_exact_close_event_uses_previous_session_not_same_day(self) -> None:
        event = SimpleNamespace(
            instrument="WDS.ASX",
            resolved_etoro_market="Sydney",
            event_at=datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
            updated_at=datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
        )
        repository = _Repository(event)
        calendar = _Calendar(
            {
                "2026-08-20": datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
                "2026-08-21": datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
                "2026-08-24": datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
            }
        )
        frame = pd.DataFrame(
            {
                "Open": [Decimal("9"), Decimal("10")],
                "High": [Decimal("10"), Decimal("11")],
                "Low": [Decimal("8"), Decimal("9")],
                "Close": [Decimal("9.5"), Decimal("10.5")],
                "Volume": [100, 200],
            },
            index=pd.DatetimeIndex(["2026-08-20", "2026-08-21"]),
        )

        acquire_and_persist_pre_event_market_context_for_event(
            repository,
            event_id="event-1",
            ticker="WDS.ASX",
            actor="tracked-event-worker",
            fetcher=lambda *_: frame,
            calendar_loader=lambda _: calendar,
        )
        payload = repository.client.calls[0][1]
        self.assertEqual(payload["input_pre_event_market_context"]["session_date"], "2026-08-21")

    def test_persisted_context_revalidation_uses_same_strict_selector(self) -> None:
        event = SimpleNamespace(
            resolved_etoro_market="Sydney",
            event_at=datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
            pre_event_market_context={
                "session_date": "2026-08-21",
                "previous_session_date": "2026-08-20",
            },
        )
        calendar = _Calendar(
            {
                "2026-08-20": datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
                "2026-08-21": datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
                "2026-08-24": datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
            }
        )
        self.assertTrue(
            persisted_pre_event_market_context_is_current(
                event, calendar_loader=lambda _: calendar
            )
        )

    def test_unknown_market_fails_before_provider_fetch(self) -> None:
        event = SimpleNamespace(
            instrument="WDS.ASX",
            resolved_etoro_market="Australia",
            event_at=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            updated_at=datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
        )
        repository = _Repository(event)
        fetch_calls = []
        with self.assertRaisesRegex(ValueError, "unsupported eToro market"):
            acquire_and_persist_pre_event_market_context_for_event(
                repository,
                event_id="event-1",
                ticker="WDS.ASX",
                actor="tracked-event-worker",
                fetcher=lambda *args: fetch_calls.append(args),
            )
        self.assertEqual(fetch_calls, [])


if __name__ == "__main__":
    unittest.main()
