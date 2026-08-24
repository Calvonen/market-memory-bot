from __future__ import annotations

from collections.abc import Sequence
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
    session_closes: Sequence[tuple[date, datetime]],
    now: datetime | None = None,
) -> SessionDateResolution:
    """Resolve the canonical pre-event session pair from a confirmed calendar.

    This function deliberately performs no weekday, holiday, ticker, country,
    or exchange inference. The caller must supply actual confirmed trading
    sessions for ``profile.calendar_id`` as ascending ``(session date, session
    close)`` pairs, with the exchange calendar's real close timestamps - never a
    fixed close time reconstructed from the market timezone, which would be
    wrong on early closes and across DST. The event's local market date is
    derived only from ``profile.market_timezone``.

    A session is *eligible* when its close is at or before ``event_at``. The two
    reference sessions are the last two eligible sessions:

        latest_closed_session   = last session with session_close <= event_at
        previous_closed_session = the eligible session before it

    Eligibility is decided by close timestamp against ``event_at``, not by
    session date against the event's trading date. That distinction is the whole
    point: an event on a session day but after that session's close must include
    that same-day session, because its daily candle is complete and is the most
    recent market state before the event. A pre-close event on the same day must
    not, because that candle is still forming. (Closes rise with session date,
    so the eligible set is always a prefix and ``previous_closed_session`` is
    simply the calendar session before ``latest_closed_session``.)

    ``event_trading_date`` - the first supplied session on or after the
    market-local event date - is still reported, and may now equal
    ``latest_closed_session`` for a post-close event.

    ``now`` is a separate, acquisition-time concern. ``event_at`` decides *which*
    sessions belong in the context; ``now`` only decides whether those sessions
    have actually finished trading *yet*, so a still-forming daily candle is
    never persisted. Passing it fails closed when either selected session is
    still open, rather than substituting an older session - reaching further
    back would persist a different, wrong baseline. Callers asking only "which
    pair does this event_at imply" (revalidating an already-persisted snapshot)
    omit it, so acquisition and revalidation always agree about the same
    ``event_at``.

    If the calendar input is unordered, duplicated, missing close timestamps, or
    does not cover the event trading date plus two eligible sessions, resolution
    fails closed.
    """
    if event_at.tzinfo is None or event_at.utcoffset() is None:
        raise ValueError("event_at must be timezone-aware")
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("now must be timezone-aware")

    session_dates = tuple(session_date for session_date, _ in session_closes)
    if len(set(session_dates)) != len(session_dates):
        raise ValueError("confirmed_session_dates must not contain duplicates")
    if tuple(sorted(session_dates)) != session_dates:
        raise ValueError("confirmed_session_dates must be strictly ascending")
    for session_date, session_close in session_closes:
        if session_close.tzinfo is None or session_close.utcoffset() is None:
            raise ValueError(
                f"session close for {session_date} must be timezone-aware"
            )

    event_local_date = event_at.astimezone(ZoneInfo(profile.market_timezone)).date()
    event_index = next(
        (index for index, session_date in enumerate(session_dates) if session_date >= event_local_date),
        None,
    )
    if event_index is None:
        raise ValueError("confirmed session calendar does not reach the event trading date")

    eligible = [
        session_date for session_date, session_close in session_closes if session_close <= event_at
    ]
    if len(eligible) < 2:
        raise ValueError("confirmed session calendar does not include two pre-event sessions")

    latest_closed_session = eligible[-1]
    previous_closed_session = eligible[-2]

    if now is not None:
        closes = dict(session_closes)
        for session_date in (previous_closed_session, latest_closed_session):
            session_close = closes[session_date]
            if session_close > now:
                raise ValueError(
                    f"pre-event session {session_date} has not closed yet "
                    f"(closes {session_close.isoformat()})"
                )

    return SessionDateResolution(
        event_local_date=event_local_date,
        event_trading_date=session_dates[event_index],
        latest_closed_session=latest_closed_session,
        previous_closed_session=previous_closed_session,
    )
