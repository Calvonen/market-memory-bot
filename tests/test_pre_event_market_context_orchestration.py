from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
import unittest

import pandas as pd

from trading_system.pre_event_market_context_orchestration import (
    acquire_and_persist_pre_event_market_context,
    acquire_and_persist_pre_event_market_context_for_event,
)


class _RpcCall:
    def __init__(self, repository) -> None:
        self.repository = repository

    def execute(self):
        self.repository.rpc_executed = True
        return object()


class _Client:
    def __init__(self, repository) -> None:
        self.repository = repository
        self.calls = []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return _RpcCall(self.repository)


class _Repository:
    def __init__(self, *, event=None) -> None:
        self.client = _Client(self)
        self.rpc_executed = False
        self.saved_event = event or SimpleNamespace(instrument="EXM.L")
        self.get_calls = []

    def get(self, event_id):
        self.get_calls.append(event_id)
        return self.saved_event


class _Calendar:
    def __init__(self, sessions) -> None:
        self.sessions = pd.DatetimeIndex(sessions)
        self.calls = []

    def sessions_in_range(self, start, end):
        self.calls.append((start, end))
        return self.sessions[(self.sessions >= start) & (self.sessions <= end)]


class PreEventMarketContextOrchestrationTests(unittest.TestCase):
    @staticmethod
    def _daily_frame() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Open": [Decimal("10"), Decimal("11")],
                "High": [Decimal("11"), Decimal("13")],
                "Low": [Decimal("9"), Decimal("10")],
                "Close": [Decimal("10.5"), Decimal("12")],
                "Volume": [100, 200],
            },
            index=pd.DatetimeIndex(["2026-08-20", "2026-08-21"]),
        )

    def test_acquires_then_persists_exact_context_with_grounded_inputs(self) -> None:
        repository = _Repository()
        fetch_calls = []

        def fetcher(ticker, period, interval):
            fetch_calls.append((ticker, period, interval))
            return self._daily_frame()

        saved = acquire_and_persist_pre_event_market_context(
            repository,
            event_id="event-1",
            ticker=" exm.l ",
            event_trading_date=date(2026, 8, 24),
            last_confirmed_closed_session_date=date(2026, 8, 21),
            previous_confirmed_closed_session_date=date(2026, 8, 20),
            market_timezone="Europe/London",
            actor="tracked-event-worker",
            fetcher=fetcher,
        )

        self.assertIs(saved, repository.saved_event)
        self.assertEqual(fetch_calls, [("EXM.L", "1mo", "1d")])
        self.assertEqual(repository.get_calls, ["event-1", "event-1"])
        self.assertEqual(len(repository.client.calls), 1)
        rpc_name, payload = repository.client.calls[0]
        self.assertEqual(rpc_name, "capture_tracked_market_event_pre_event_context")
        self.assertEqual(payload["input_event_id"], "event-1")
        self.assertEqual(payload["input_market_timezone"], "Europe/London")
        self.assertEqual(payload["input_actor"], "tracked-event-worker")
        self.assertEqual(
            payload["input_pre_event_market_context"],
            {
                "schema_version": 1,
                "session_date": "2026-08-21",
                "previous_session_date": "2026-08-20",
                "open_price": "11",
                "high_price": "13",
                "low_price": "10",
                "close_price": "12",
                "previous_close_price": "10.5",
                "session_return_pct": str((Decimal("12") / Decimal("11") - 1) * 100),
                "close_to_close_return_pct": str((Decimal("12") / Decimal("10.5") - 1) * 100),
                "close_to_close_direction": "up",
            },
        )

    def test_auto_entrypoint_resolves_sydney_sessions_before_acquisition(self) -> None:
        event = SimpleNamespace(
            instrument="WDS.ASX",
            resolved_etoro_market="Sydney",
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
        )
        repository = _Repository(event=event)
        calendar = _Calendar(["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"])
        calendar_ids = []
        fetch_calls = []

        def calendar_loader(calendar_id):
            calendar_ids.append(calendar_id)
            return calendar

        def fetcher(ticker, period, interval):
            fetch_calls.append((ticker, period, interval))
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

        saved = acquire_and_persist_pre_event_market_context_for_event(
            repository,
            event_id="event-1",
            ticker="WDS.ASX",
            actor="tracked-event-worker",
            fetcher=fetcher,
            calendar_loader=calendar_loader,
        )

        self.assertIs(saved, event)
        self.assertEqual(calendar_ids, ["XASX"])
        self.assertEqual(fetch_calls, [("WDS.ASX", "1mo", "1d")])
        rpc_name, payload = repository.client.calls[0]
        self.assertEqual(rpc_name, "capture_tracked_market_event_pre_event_context")
        self.assertEqual(payload["input_market_timezone"], "Australia/Sydney")
        self.assertEqual(payload["input_pre_event_market_context"]["session_date"], "2026-08-24")
        self.assertEqual(payload["input_pre_event_market_context"]["previous_session_date"], "2026-08-21")

    def test_auto_entrypoint_rejects_unresolved_market_before_calendar_or_fetch(self) -> None:
        event = SimpleNamespace(
            instrument="WDS.ASX",
            resolved_etoro_market=None,
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
        )
        repository = _Repository(event=event)
        calendar_calls = []
        fetch_calls = []

        with self.assertRaisesRegex(ValueError, "no resolved_etoro_market"):
            acquire_and_persist_pre_event_market_context_for_event(
                repository,
                event_id="event-1",
                ticker="WDS.ASX",
                actor="tracked-event-worker",
                fetcher=lambda *args: fetch_calls.append(args),
                calendar_loader=lambda calendar_id: calendar_calls.append(calendar_id),
            )

        self.assertEqual(calendar_calls, [])
        self.assertEqual(fetch_calls, [])
        self.assertEqual(repository.client.calls, [])

    def test_auto_entrypoint_rejects_unknown_broker_market_before_calendar_or_fetch(self) -> None:
        event = SimpleNamespace(
            instrument="WDS.ASX",
            resolved_etoro_market="Australia",
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
        )
        repository = _Repository(event=event)
        calendar_calls = []

        with self.assertRaisesRegex(ValueError, "unsupported eToro market"):
            acquire_and_persist_pre_event_market_context_for_event(
                repository,
                event_id="event-1",
                ticker="WDS.ASX",
                actor="tracked-event-worker",
                calendar_loader=lambda calendar_id: calendar_calls.append(calendar_id),
            )

        self.assertEqual(calendar_calls, [])
        self.assertEqual(repository.client.calls, [])

    def test_rejects_ticker_that_does_not_match_canonical_event_before_fetch(self) -> None:
        repository = _Repository(event=SimpleNamespace(instrument="EXM.L"))
        fetch_calls = []

        def fetcher(ticker, period, interval):
            fetch_calls.append((ticker, period, interval))
            return self._daily_frame()

        with self.assertRaisesRegex(ValueError, "ticker does not match tracked event instrument"):
            acquire_and_persist_pre_event_market_context(
                repository,
                event_id="event-1",
                ticker="OTHER.L",
                event_trading_date=date(2026, 8, 24),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                previous_confirmed_closed_session_date=date(2026, 8, 20),
                market_timezone="Europe/London",
                actor="tracked-event-worker",
                fetcher=fetcher,
            )

        self.assertEqual(fetch_calls, [])
        self.assertEqual(repository.client.calls, [])
        self.assertFalse(repository.rpc_executed)
        self.assertEqual(repository.get_calls, ["event-1"])

    def test_acquisition_failure_happens_before_any_persistence_write(self) -> None:
        repository = _Repository()

        def fetcher(ticker, period, interval):
            return pd.DataFrame(index=pd.DatetimeIndex(["2026-08-21"]))

        with self.assertRaises(ValueError):
            acquire_and_persist_pre_event_market_context(
                repository,
                event_id="event-1",
                ticker="EXM.L",
                event_trading_date=date(2026, 8, 24),
                last_confirmed_closed_session_date=date(2026, 8, 21),
                previous_confirmed_closed_session_date=date(2026, 8, 20),
                market_timezone="Europe/London",
                actor="tracked-event-worker",
                fetcher=fetcher,
            )

        self.assertEqual(repository.client.calls, [])
        self.assertFalse(repository.rpc_executed)
        self.assertEqual(repository.get_calls, ["event-1"])


if __name__ == "__main__":
    unittest.main()
