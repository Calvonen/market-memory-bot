from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import unittest

import pandas as pd

from trading_system.pre_event_market_context_orchestration import (
    acquire_and_persist_pre_event_market_context,
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
    def __init__(self) -> None:
        self.client = _Client(self)
        self.rpc_executed = False
        self.saved_event = object()
        self.get_calls = []

    def get(self, event_id):
        self.get_calls.append(event_id)
        return self.saved_event


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
        self.assertEqual(repository.get_calls, ["event-1"])
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
        self.assertEqual(repository.get_calls, [])


if __name__ == "__main__":
    unittest.main()
