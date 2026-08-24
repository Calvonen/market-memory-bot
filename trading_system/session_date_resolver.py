from __future__ import annotations

from collections.abc import Mapping
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
    session_closes: Mapping[date, datetime] | None = None,
    now: datetime | None = None,
) -> SessionDateResolution:
    """Resolve session context from an explicit confirmed calendar.

    This function deliberately performs no weekday, holiday, ticker, country,
    or exchange inference. The caller must supply actual confirmed trading
    session dates for ``profile.calendar_id``. The event's local market date is
    derived only from ``profile.market_timezone``.

    ``event_trading_date`` is the first supplied session on or after the
    market-local event date. The two pre-event reference sessions are the two
    supplied sessions immediately before that trading date. If the calendar
    input is missing, duplicated, unordered, or does not cover all three dates,
    resolution fails closed.

    Supplying ``session_closes`` and ``now`` together additionally enforces that
    both selected sessions have actually finished trading: session ordering
    alone only proves a session is scheduled before the event, not that its
    close has passed at acquisition time, and an in-progress session's daily
    candle is still partial. When the latest scheduled pre-event session is
    still open this fails closed rather than substituting an older session -
    the canonical context is defined as the two sessions immediately preceding
    the event trading date, so silently reaching further back would persist a
    different, wrong baseline. Callers that only need the date-only contract
    (for example revalidating an already-persisted snapshot) omit both.
    """
    if event_at.tzinfo is None or event_at.utcoffset() is None:
        raise ValueError("event_at must be timezone-aware")
    if (session_closes is None) != (now is None):
        raise ValueError("session_closes and now must be supplied together")
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("now must be timezone-aware")
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

    latest_closed_session = confirmed_session_dates[event_index - 1]
    previous_closed_session = confirmed_session_dates[event_index - 2]

    if session_closes is not None and now is not None:
        for session_date in (previous_closed_session, latest_closed_session):
            session_close = session_closes.get(session_date)
            if session_close is None:
                raise ValueError(
                    f"confirmed session calendar has no close time for session {session_date}"
                )
            if session_close.tzinfo is None or session_close.utcoffset() is None:
                raise ValueError("session close times must be timezone-aware")
            if session_close > now:
                raise ValueError(
                    f"pre-event session {session_date} has not closed yet "
                    f"(closes {session_close.isoformat()})"
                )

    return SessionDateResolution(
        event_local_date=event_local_date,
        event_trading_date=confirmed_session_dates[event_index],
        latest_closed_session=latest_closed_session,
        previous_closed_session=previous_closed_session,
    )
