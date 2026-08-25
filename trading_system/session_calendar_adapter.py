from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Callable, Protocol

import exchange_calendars as xcals

from trading_system.market_session_profile import MarketSessionProfile


class SessionCalendar(Protocol):
    def sessions_in_range(self, start: str, end: str): ...

    def session_close(self, session): ...


def confirmed_session_dates(
    profile: MarketSessionProfile,
    *,
    start_date: date,
    end_date: date,
    calendar_loader: Callable[[str], SessionCalendar] = xcals.get_calendar,
) -> tuple[date, ...]:
    """Return confirmed exchange session dates for one explicit market profile.

    ``profile.calendar_id`` is passed directly to the exchange-calendar library;
    this adapter performs no aliases, ticker/country inference, weekday rules,
    holiday rules, or fallback mapping of its own. Unknown calendars and
    out-of-range calendar data are allowed to fail closed from the provider.
    """
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    calendar = calendar_loader(profile.calendar_id)
    sessions = calendar.sessions_in_range(start_date.isoformat(), end_date.isoformat())
    return tuple(session.date() for session in sessions)


def confirmed_session_closes(
    profile: MarketSessionProfile,
    *,
    start_date: date,
    end_date: date,
    calendar_loader: Callable[[str], SessionCalendar] = xcals.get_calendar,
) -> tuple[tuple[date, datetime], ...]:
    """Return (session date, actual session close) pairs for one market profile.

    Session *dates* alone cannot tell a caller whether a session has finished:
    the session immediately before an event can still be trading when the
    pre-event context is acquired, and freezing an in-progress daily candle
    into an immutable snapshot would misrepresent the pre-event baseline. The
    exchange calendar owns the real close timestamp - including early closes -
    so it is read here rather than reconstructed from the market timezone.

    Closes are normalised to timezone-aware UTC. Like ``confirmed_session_dates``
    this adapter performs no aliases, ticker/country inference, weekday rules,
    holiday rules, or fallback mapping of its own.
    """
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    calendar = calendar_loader(profile.calendar_id)
    sessions = calendar.sessions_in_range(start_date.isoformat(), end_date.isoformat())

    closes: list[tuple[date, datetime]] = []
    for session in sessions:
        close = calendar.session_close(session)
        close_at = close.to_pydatetime() if hasattr(close, "to_pydatetime") else close
        if close_at.tzinfo is None or close_at.utcoffset() is None:
            raise ValueError("exchange calendar returned a timezone-naive session close")
        closes.append((session.date(), close_at.astimezone(UTC)))
    return tuple(closes)
