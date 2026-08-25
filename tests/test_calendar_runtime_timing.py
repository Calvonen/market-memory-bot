import unittest
from datetime import UTC, date, datetime

from trading_system.calendar_repository import CalendarEvent, CalendarEventStatus
from trading_system.calendar_runtime_timing import (
    CalendarRuntimeTimingUnavailable,
    FinnhubCalendarRuntimeTimingResolver,
)
from trading_system.tracked_event_repository import TrackedEventTimeStatus


class _Response:
    def __init__(self, payload, *, ok=True, status_code=200, text="") -> None:
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class _Calendar:
    def __init__(self, *, session_open: datetime, session_close: datetime, sessions=("2026-08-25",)) -> None:
        self._session_open = session_open
        self._session_close = session_close
        self._sessions = sessions

    def sessions_in_range(self, _start, _end):
        return self._sessions

    def session_open(self, _session):
        return self._session_open

    def session_close(self, _session):
        return self._session_close


def _event(**overrides) -> CalendarEvent:
    values = dict(
        calendar_event_id="11111111-1111-1111-1111-111111111111",
        company_name="DICK'S SPORTING GOODS INC",
        instrument="DKS",
        market="USA",
        event_type="earnings",
        scheduled_date=date(2026, 8, 25),
        source="finnhub",
        occurrence_key="2027Q2",
        status=CalendarEventStatus.TRACKED,
    )
    values.update(overrides)
    return CalendarEvent(**values)


class CalendarRuntimeTimingTests(unittest.TestCase):
    def _resolver(self, row, *, session_open=None, session_close=None):
        session_open = session_open or datetime(2026, 8, 25, 13, 30, tzinfo=UTC)
        session_close = session_close or datetime(2026, 8, 25, 20, 0, tzinfo=UTC)
        calendar = _Calendar(session_open=session_open, session_close=session_close)
        calls = []

        def http_get(url, *, params, timeout):
            calls.append((url, params, timeout))
            return _Response({"earningsCalendar": [row]})

        return (
            FinnhubCalendarRuntimeTimingResolver(
                api_key="test-key",
                http_get=http_get,
                calendar_loader=lambda calendar_id: calendar,
            ),
            calls,
        )

    def test_bmo_uses_actual_exchange_session_open(self) -> None:
        resolver, calls = self._resolver(
            {
                "symbol": "DKS",
                "date": "2026-08-25",
                "hour": "bmo",
                "year": 2027,
                "quarter": 2,
            },
            session_open=datetime(2026, 8, 25, 13, 30, tzinfo=UTC),
        )

        timing = resolver.resolve(_event())

        self.assertEqual(timing.event_at, datetime(2026, 8, 25, 13, 30, tzinfo=UTC))
        self.assertEqual(timing.event_time_status, TrackedEventTimeStatus.ESTIMATED)
        self.assertEqual(timing.provider_timing, "bmo")
        self.assertEqual(calls[0][1]["symbol"], "DKS")
        self.assertEqual(calls[0][1]["from"], "2026-08-25")
        self.assertEqual(calls[0][1]["to"], "2026-08-25")

    def test_amc_uses_actual_close_plus_one_microsecond(self) -> None:
        resolver, _calls = self._resolver(
            {
                "symbol": "DKS",
                "date": "2026-08-25",
                "hour": "amc",
                "year": 2027,
                "quarter": 2,
            },
            session_close=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        )

        timing = resolver.resolve(_event())

        self.assertEqual(timing.event_at, datetime(2026, 8, 25, 20, 0, 0, 1, tzinfo=UTC))
        self.assertEqual(timing.provider_timing, "amc")

    def test_dmh_fails_closed_instead_of_guessing_intraday_time(self) -> None:
        resolver, _calls = self._resolver(
            {
                "symbol": "DKS",
                "date": "2026-08-25",
                "hour": "dmh",
                "year": 2027,
                "quarter": 2,
            }
        )

        with self.assertRaisesRegex(CalendarRuntimeTimingUnavailable, "not precise enough"):
            resolver.resolve(_event())

    def test_missing_hour_fails_closed(self) -> None:
        resolver, _calls = self._resolver(
            {
                "symbol": "DKS",
                "date": "2026-08-25",
                "year": 2027,
                "quarter": 2,
            }
        )

        with self.assertRaisesRegex(CalendarRuntimeTimingUnavailable, "not precise enough"):
            resolver.resolve(_event())

    def test_wrong_fiscal_occurrence_is_not_accepted(self) -> None:
        resolver, _calls = self._resolver(
            {
                "symbol": "DKS",
                "date": "2026-08-25",
                "hour": "bmo",
                "year": 2026,
                "quarter": 2,
            }
        )

        with self.assertRaisesRegex(CalendarRuntimeTimingUnavailable, "missing or ambiguous"):
            resolver.resolve(_event())

    def test_non_us_market_is_not_inferred(self) -> None:
        resolver, calls = self._resolver(
            {
                "symbol": "DKS",
                "date": "2026-08-25",
                "hour": "bmo",
                "year": 2027,
                "quarter": 2,
            }
        )

        with self.assertRaisesRegex(CalendarRuntimeTimingUnavailable, "not grounded"):
            resolver.resolve(_event(market="Unknown"))
        self.assertEqual(calls, [])

    def test_non_trading_date_fails_closed(self) -> None:
        calendar = _Calendar(
            session_open=datetime(2026, 8, 25, 13, 30, tzinfo=UTC),
            session_close=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
            sessions=(),
        )

        def http_get(_url, *, params, timeout):
            return _Response(
                {
                    "earningsCalendar": [
                        {
                            "symbol": "DKS",
                            "date": "2026-08-25",
                            "hour": "bmo",
                            "year": 2027,
                            "quarter": 2,
                        }
                    ]
                }
            )

        resolver = FinnhubCalendarRuntimeTimingResolver(
            api_key="test-key",
            http_get=http_get,
            calendar_loader=lambda _calendar_id: calendar,
        )

        with self.assertRaisesRegex(CalendarRuntimeTimingUnavailable, "trading session"):
            resolver.resolve(_event())


if __name__ == "__main__":
    unittest.main()
