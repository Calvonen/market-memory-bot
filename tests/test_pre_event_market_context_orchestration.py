from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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


def _xasx_close(session_date: date) -> datetime:
    """The real XASX close shape: 06:00Z on the session's own date."""
    return datetime(session_date.year, session_date.month, session_date.day, 6, 0, tzinfo=UTC)


class _Calendar:
    def __init__(self, sessions, *, closes=None) -> None:
        self.sessions = pd.DatetimeIndex(sessions)
        self.calls = []
        self.closes = {
            pd.Timestamp(session_date).date(): close
            for session_date, close in (closes or {}).items()
        }

    def sessions_in_range(self, start, end):
        self.calls.append((start, end))
        return self.sessions[(self.sessions >= start) & (self.sessions <= end)]

    def session_close(self, session):
        session_date = pd.Timestamp(session).date()
        # Default to each session's own real close. A blanket "closed long ago"
        # would make every session in range eligible and let selection run past
        # the event, so the default has to track the session date.
        return self.closes.get(session_date, _xasx_close(session_date))


class _FrozenNow:
    """Pin acquisition's wall clock so the now-gate is testable."""

    def __init__(self, instant: datetime) -> None:
        self.instant = instant

    def now(self, tz=None):
        return self.instant.astimezone(tz) if tz else self.instant


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
            provider_symbol="EXM.L",
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
        version = datetime(2026, 8, 24, 14, 47, tzinfo=UTC)
        event = SimpleNamespace(
            instrument="WDS.ASX",
            resolved_etoro_market="Sydney",
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            updated_at=version,
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

        # Pinned so the now-gate does not depend on the wall clock: this same
        # calendar is only acquirable after 2026-08-24's 06:00Z close.
        with patch(
            "trading_system.pre_event_market_context_orchestration.datetime",
            _FrozenNow(datetime(2026, 8, 24, 23, 0, tzinfo=UTC)),
        ):
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
        self.assertEqual(fetch_calls, [("WDS.AX", "1mo", "1d")])
        self.assertEqual(repository.get_calls, ["event-1", "event-1"])
        rpc_name, payload = repository.client.calls[0]
        self.assertEqual(
            rpc_name,
            "capture_tracked_market_event_pre_event_context_if_current",
        )
        self.assertEqual(payload["input_market_timezone"], "Australia/Sydney")
        self.assertEqual(payload["input_expected_updated_at"], version.isoformat())
        self.assertEqual(payload["input_pre_event_market_context"]["session_date"], "2026-08-24")
        self.assertEqual(payload["input_pre_event_market_context"]["previous_session_date"], "2026-08-21")

    @staticmethod
    def _ohlcv(dates):
        return pd.DataFrame(
            {
                "Open": [Decimal("10"), Decimal("11")],
                "High": [Decimal("11"), Decimal("13")],
                "Low": [Decimal("9"), Decimal("10")],
                "Close": [Decimal("10.5"), Decimal("12")],
                "Volume": [100, 200],
            },
            index=pd.DatetimeIndex(dates),
        )

    def _acquire_at(self, *, event_at, sessions, now, closes=None, ohlcv_dates=None):
        """Run the auto entrypoint with a pinned acquisition clock."""
        event = SimpleNamespace(
            instrument="WDS.ASX",
            resolved_etoro_market="Sydney",
            event_at=event_at,
            updated_at=datetime(2026, 8, 24, 14, 47, tzinfo=UTC),
        )
        repository = _Repository(event=event)
        calendar = _Calendar(sessions, closes=closes)
        fetch_calls = []

        def fetcher(ticker, period, interval):
            fetch_calls.append((ticker, period, interval))
            return self._ohlcv(ohlcv_dates or [])

        with patch(
            "trading_system.pre_event_market_context_orchestration.datetime",
            _FrozenNow(now),
        ):
            acquire_and_persist_pre_event_market_context_for_event(
                repository,
                event_id="event-1",
                ticker="WDS.ASX",
                actor="tracked-event-worker",
                fetcher=fetcher,
                calendar_loader=lambda calendar_id: calendar,
            )

        return repository, fetch_calls

    def test_auto_entrypoint_waits_when_latest_pre_event_session_is_still_open(self) -> None:
        # event_at is next-day, so Monday 2026-08-24 is eligible by event_at.
        # But acquisition is running before Monday's 06:00Z close, so Yahoo's
        # daily row for it is still a partial intraday candle. Acquisition must
        # not fetch or persist - and must not silently reach further back to
        # the already-closed 2026-08-21/2026-08-20 pair, which is a different
        # baseline than the one this event_at implies.
        with self.assertRaisesRegex(ValueError, "2026-08-24 has not closed yet"):
            self._acquire_at(
                event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
                sessions=["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"],
                now=datetime(2026, 8, 24, 5, 59, tzinfo=UTC),
            )

    def test_auto_entrypoint_proceeds_once_that_session_has_closed(self) -> None:
        # Same event and calendar, only the acquisition clock moved past
        # Monday's close: the exact same session pair is now accepted.
        repository, fetch_calls = self._acquire_at(
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            sessions=["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"],
            now=datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
            ohlcv_dates=["2026-08-21", "2026-08-24"],
        )

        self.assertEqual(fetch_calls, [("WDS.AX", "1mo", "1d")])
        _rpc_name, payload = repository.client.calls[0]
        self.assertEqual(payload["input_pre_event_market_context"]["session_date"], "2026-08-24")
        self.assertEqual(
            payload["input_pre_event_market_context"]["previous_session_date"], "2026-08-21"
        )

    def test_same_day_event_after_the_close_uses_the_event_day_session(self) -> None:
        # Monday event an hour after Monday's close: the event-day session is
        # complete, so it is the latest reference and Friday is the previous.
        repository, _fetch_calls = self._acquire_at(
            event_at=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            sessions=["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"],
            now=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            ohlcv_dates=["2026-08-21", "2026-08-24"],
        )

        _rpc_name, payload = repository.client.calls[0]
        self.assertEqual(payload["input_pre_event_market_context"]["session_date"], "2026-08-24")
        self.assertEqual(
            payload["input_pre_event_market_context"]["previous_session_date"], "2026-08-21"
        )

    def test_same_day_event_before_the_close_excludes_the_event_day_session(self) -> None:
        # Monday event an hour before Monday's close: that candle is still
        # forming, so the pair is Friday/Thursday.
        repository, _fetch_calls = self._acquire_at(
            event_at=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            sessions=["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"],
            now=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            ohlcv_dates=["2026-08-20", "2026-08-21"],
        )

        _rpc_name, payload = repository.client.calls[0]
        self.assertEqual(payload["input_pre_event_market_context"]["session_date"], "2026-08-21")
        self.assertEqual(
            payload["input_pre_event_market_context"]["previous_session_date"], "2026-08-20"
        )

    def test_auto_entrypoint_uses_the_real_early_close_for_same_day_eligibility(self) -> None:
        # 2026-12-24 is an XASX early close at 03:10Z. A 04:00Z event that day
        # is after it, so the early-close session is the latest reference; a
        # fixed close inferred from the market timezone would exclude it.
        repository, _fetch_calls = self._acquire_at(
            event_at=datetime(2026, 12, 24, 4, 0, tzinfo=UTC),
            sessions=["2026-12-22", "2026-12-23", "2026-12-24", "2026-12-29"],
            closes={"2026-12-24": datetime(2026, 12, 24, 3, 10, tzinfo=UTC)},
            now=datetime(2026, 12, 24, 4, 0, tzinfo=UTC),
            ohlcv_dates=["2026-12-23", "2026-12-24"],
        )

        _rpc_name, payload = repository.client.calls[0]
        self.assertEqual(payload["input_pre_event_market_context"]["session_date"], "2026-12-24")
        self.assertEqual(
            payload["input_pre_event_market_context"]["previous_session_date"], "2026-12-23"
        )

    def test_auto_entrypoint_skips_weekend_and_holiday_gaps_using_real_closes(self) -> None:
        # Monday 2026-12-28 is not an XASX session and 2026-12-25 is a holiday,
        # so a Sunday-evening event resolves forward to 2026-12-29 with the
        # 2026-12-24/2026-12-23 pair behind it.
        repository, _fetch_calls = self._acquire_at(
            event_at=datetime(2026, 12, 27, 22, 0, tzinfo=UTC),
            sessions=["2026-12-22", "2026-12-23", "2026-12-24", "2026-12-29", "2026-12-30"],
            now=datetime(2026, 12, 27, 22, 0, tzinfo=UTC),
            ohlcv_dates=["2026-12-23", "2026-12-24"],
        )

        _rpc_name, payload = repository.client.calls[0]
        self.assertEqual(payload["input_pre_event_market_context"]["session_date"], "2026-12-24")
        self.assertEqual(
            payload["input_pre_event_market_context"]["previous_session_date"], "2026-12-23"
        )

    def test_auto_entrypoint_rejects_unresolved_market_before_calendar_or_fetch(self) -> None:
        event = SimpleNamespace(
            instrument="WDS.ASX",
            resolved_etoro_market=None,
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 8, 24, 14, 47, tzinfo=UTC),
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
            updated_at=datetime(2026, 8, 24, 14, 47, tzinfo=UTC),
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

    def test_auto_entrypoint_requires_event_version_before_external_calls(self) -> None:
        event = SimpleNamespace(
            instrument="WDS.ASX",
            resolved_etoro_market="Sydney",
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            updated_at=None,
        )
        repository = _Repository(event=event)
        calendar_calls = []
        fetch_calls = []

        with self.assertRaisesRegex(ValueError, "no updated_at version"):
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
                provider_symbol="OTHER.L",
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

    def test_persisted_context_is_current_when_event_at_unchanged_since_capture(self) -> None:
        event = SimpleNamespace(
            resolved_etoro_market="Sydney",
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            pre_event_market_context={
                "schema_version": 1,
                "session_date": "2026-08-24",
                "previous_session_date": "2026-08-21",
            },
        )
        calendar = _Calendar(["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26"])

        is_current = persisted_pre_event_market_context_is_current(
            event,
            calendar_loader=lambda calendar_id: calendar,
        )

        self.assertTrue(is_current)

    def test_yahoo_is_queried_with_the_provider_symbol_not_the_broker_symbol(self) -> None:
        # eToro WDS.ASX is Yahoo WDS.AX. The persisted broker instrument must
        # never be handed to the data provider as if it were a provider ticker.
        repository, fetch_calls = self._acquire_at(
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            sessions=["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"],
            now=datetime(2026, 8, 24, 23, 0, tzinfo=UTC),
            ohlcv_dates=["2026-08-21", "2026-08-24"],
        )

        self.assertEqual(fetch_calls, [("WDS.AX", "1mo", "1d")])
        self.assertNotIn("WDS.ASX", [symbol for symbol, _p, _i in fetch_calls])
        # The event itself keeps its broker identity - translation is for the
        # provider call only and never rewrites what is tracked or persisted.
        self.assertEqual(repository.saved_event.instrument, "WDS.ASX")
        _name, payload = repository.client.calls[0]
        self.assertNotIn("WDS.AX", str(payload["input_pre_event_market_context"]))

    def test_a_second_sydney_instrument_resolves_through_the_same_policy(self) -> None:
        # Guards against a WDS-only mapping: the grounded Sydney profile must
        # translate every instrument carrying the market's broker suffix.
        event = SimpleNamespace(
            instrument="NHF.ASX",
            resolved_etoro_market="Sydney",
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 8, 24, 14, 47, tzinfo=UTC),
        )
        repository = _Repository(event=event)
        fetch_calls = []

        def fetcher(ticker, period, interval):
            fetch_calls.append((ticker, period, interval))
            return self._ohlcv(["2026-08-21", "2026-08-24"])

        with patch(
            "trading_system.pre_event_market_context_orchestration.datetime",
            _FrozenNow(datetime(2026, 8, 24, 23, 0, tzinfo=UTC)),
        ):
            acquire_and_persist_pre_event_market_context_for_event(
                repository,
                event_id="event-1",
                ticker="NHF.ASX",
                actor="tracked-event-worker",
                fetcher=fetcher,
                calendar_loader=lambda calendar_id: _Calendar(
                    ["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"]
                ),
            )

        self.assertEqual(fetch_calls, [("NHF.AX", "1mo", "1d")])
        self.assertEqual(repository.saved_event.instrument, "NHF.ASX")

    def test_unmappable_broker_symbol_fails_closed_before_any_fetch(self) -> None:
        # An instrument that does not carry the grounded market's broker suffix
        # is refused before the provider is contacted - never guessed at.
        event = SimpleNamespace(
            instrument="WDS.XYZ",
            resolved_etoro_market="Sydney",
            event_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 8, 24, 14, 47, tzinfo=UTC),
        )
        repository = _Repository(event=event)
        fetch_calls = []
        calendar_calls = []

        with self.assertRaisesRegex(ValueError, "does not carry the Sydney broker suffix"):
            acquire_and_persist_pre_event_market_context_for_event(
                repository,
                event_id="event-1",
                ticker="WDS.XYZ",
                actor="tracked-event-worker",
                fetcher=lambda *args: fetch_calls.append(args),
                calendar_loader=lambda calendar_id: calendar_calls.append(calendar_id),
            )

        self.assertEqual(fetch_calls, [])
        self.assertEqual(calendar_calls, [])
        self.assertEqual(repository.client.calls, [])
        self.assertFalse(repository.rpc_executed)

    def test_only_a_post_close_event_can_emit_a_same_day_snapshot(self) -> None:
        # The DB capture RPC permits session_date == the event's local market
        # date but cannot verify the close time itself (no exchange calendar in
        # Postgres), so this orchestration is the trust boundary that proves it.
        # A post-close event may emit a same-day snapshot; a pre-close one must
        # never be able to, and no snapshot may ever name a later session.
        sessions = ["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"]

        post_close, _ = self._acquire_at(
            event_at=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            sessions=sessions,
            now=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            ohlcv_dates=["2026-08-21", "2026-08-24"],
        )
        _name, payload = post_close.client.calls[0]
        self.assertEqual(payload["input_pre_event_market_context"]["session_date"], "2026-08-24")

        pre_close, _ = self._acquire_at(
            event_at=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            sessions=sessions,
            now=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            ohlcv_dates=["2026-08-20", "2026-08-21"],
        )
        _name, payload = pre_close.client.calls[0]
        snapshot = payload["input_pre_event_market_context"]
        self.assertNotEqual(snapshot["session_date"], "2026-08-24")
        self.assertEqual(snapshot["session_date"], "2026-08-21")
        # Previous session is strictly earlier and also before the event day.
        self.assertLess(snapshot["previous_session_date"], snapshot["session_date"])
        self.assertLess(snapshot["previous_session_date"], "2026-08-24")

    def test_a_snapshot_never_names_a_session_after_the_event(self) -> None:
        # The one thing the DB still rejects outright is a session dated after
        # the event's local date; acquisition must never produce one.
        repository, _ = self._acquire_at(
            event_at=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            sessions=["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26"],
            now=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
            ohlcv_dates=["2026-08-21", "2026-08-24"],
        )

        _name, payload = repository.client.calls[0]
        snapshot = payload["input_pre_event_market_context"]
        self.assertLessEqual(snapshot["session_date"], "2026-08-24")
        self.assertNotIn(snapshot["session_date"], ("2026-08-25", "2026-08-26"))

    def test_revalidation_agrees_with_acquisition_about_the_same_event_at(self) -> None:
        # The regression this guards: acquisition selecting Monday/Friday for a
        # post-close Monday event while revalidation expected Friday/Thursday,
        # which would fail a healthy restart as "stale". Both run the same
        # canonical selection, so for each event_at the pair acquisition
        # persists is exactly the pair revalidation accepts.
        sessions = ["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26"]

        for label, event_at, ohlcv_dates in (
            ("monday post-close", datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
             ["2026-08-21", "2026-08-24"]),
            ("monday pre-close", datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
             ["2026-08-20", "2026-08-21"]),
            ("next trading day", datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
             ["2026-08-21", "2026-08-24"]),
        ):
            with self.subTest(label=label):
                repository, _fetch_calls = self._acquire_at(
                    event_at=event_at,
                    sessions=sessions,
                    now=event_at,
                    ohlcv_dates=ohlcv_dates,
                )
                _rpc_name, payload = repository.client.calls[0]
                persisted = payload["input_pre_event_market_context"]

                # Feed exactly what acquisition persisted back to revalidation.
                restarted = SimpleNamespace(
                    resolved_etoro_market="Sydney",
                    event_at=event_at,
                    pre_event_market_context=persisted,
                )
                self.assertTrue(
                    persisted_pre_event_market_context_is_current(
                        restarted,
                        calendar_loader=lambda calendar_id: _Calendar(sessions),
                    )
                )

    def test_revalidation_uses_close_based_selection_for_a_post_close_event(self) -> None:
        # Directly pins the half that used to be date-only: a post-close Monday
        # event revalidates against the Monday/Friday pair, not Friday/Thursday.
        sessions = ["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"]
        event_at = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)

        self.assertTrue(
            persisted_pre_event_market_context_is_current(
                SimpleNamespace(
                    resolved_etoro_market="Sydney",
                    event_at=event_at,
                    pre_event_market_context={
                        "schema_version": 1,
                        "session_date": "2026-08-24",
                        "previous_session_date": "2026-08-21",
                    },
                ),
                calendar_loader=lambda calendar_id: _Calendar(sessions),
            )
        )
        self.assertFalse(
            persisted_pre_event_market_context_is_current(
                SimpleNamespace(
                    resolved_etoro_market="Sydney",
                    event_at=event_at,
                    pre_event_market_context={
                        "schema_version": 1,
                        "session_date": "2026-08-21",
                        "previous_session_date": "2026-08-20",
                    },
                ),
                calendar_loader=lambda calendar_id: _Calendar(sessions),
            )
        )

    def test_persisted_context_is_stale_after_event_at_moves_to_later_trading_date(self) -> None:
        # event_at moved a session later after the context was captured for the
        # original event_at (upsert_tracked_market_event still allows editing
        # event_at while TRACKED with no reference yet). The last two closed
        # sessions before the new event_at no longer match the persisted
        # snapshot's session_date/previous_session_date, so it must be treated
        # as stale rather than reused for monitoring.
        event = SimpleNamespace(
            resolved_etoro_market="Sydney",
            event_at=datetime(2026, 8, 26, 0, 0, tzinfo=UTC),
            pre_event_market_context={
                "schema_version": 1,
                "session_date": "2026-08-24",
                "previous_session_date": "2026-08-21",
            },
        )
        calendar = _Calendar(["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26"])

        is_current = persisted_pre_event_market_context_is_current(
            event,
            calendar_loader=lambda calendar_id: calendar,
        )

        self.assertFalse(is_current)

    def test_acquisition_failure_happens_before_any_persistence_write(self) -> None:
        repository = _Repository()

        def fetcher(ticker, period, interval):
            return pd.DataFrame(index=pd.DatetimeIndex(["2026-08-21"]))

        with self.assertRaises(ValueError):
            acquire_and_persist_pre_event_market_context(
                repository,
                event_id="event-1",
                ticker="EXM.L",
                provider_symbol="EXM.L",
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
