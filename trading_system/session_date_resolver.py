from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from trading_system.market_session_profile import MarketSessionProfile


@dataclass(frozen=True)
class SessionDateResolution:
    event_local_date: date
    event_trading_date: date
    latest_closed_session: date
    previous_closed_session: date


def resolve_session_dates(
    event_at: datetime,
    *,
    profile: MarketSessionProfile,
    confirmed_session_dates: tuple[date, ...],
) -> SessionDateResolution:
    """Resolve date-only session context from an explicit confirmed calendar.

    This function deliberately performs no weekday, holiday, ticker, country,
    or exchange inference. The caller must supply actual confirmed trading
    session dates for ``profile.calendar_id``. The event's local market date is
    derived only from ``profile.market_timezone``.

    ``event_trading_date`` is the first supplied session on or after the
    market-local event date. The two pre-event reference sessions are the two
    supplied sessions immediately before that trading date. If the calendar
    input is missing, duplicated, unordered, or does not cover all three dates,
    resolution fails closed.

    Intraday open/close cutoffs are intentionally out of scope for this
    date-only contract; a later session-calendar adapter owns those boundaries.
    """
    if event_at.tzinfo is None or event_at.utcoffset() is None:
        raise ValueError("event_at must be timezone-aware")
    if len(set(confirmed_session_dates)) != len(confirmed_session_dates):
        raise ValueError("confirmed_session_dates must not contain duplicates")
    if tuple(sorted(confirmed_session_dates)) != confirmed_session_dates:
        raise ValueError("confirmed_session_dates must be strictly ascending")

    event_local_date = event_at.astimezone(ZoneInfo(profile.market_timezone)).date()
    event_index = next(
        (index for index, session_date in enumerate(confirmed_session_dates) if session_date >= event_local_date),
        None,
    )
    if event_index is None:
        raise ValueError("confirmed session calendar does not reach the event trading date")
    if event_index < 2:
        raise ValueError("confirmed session calendar does not include two pre-event sessions")

    return SessionDateResolution(
        event_local_date=event_local_date,
        event_trading_date=confirmed_session_dates[event_index],
        latest_closed_session=confirmed_session_dates[event_index - 1],
        previous_closed_session=confirmed_session_dates[event_index - 2],
    )
