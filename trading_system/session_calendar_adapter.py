from __future__ import annotations

from datetime import date
from typing import Callable, Protocol

import exchange_calendars as xcals

from trading_system.market_session_profile import MarketSessionProfile


class SessionCalendar(Protocol):
    def sessions_in_range(self, start: str, end: str): ...


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
