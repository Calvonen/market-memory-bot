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

    The caller supplies actual confirmed sessions for ``profile.calendar_id`` as
    ascending ``(session date, session close)`` pairs using the exchange
    calendar's real close timestamps. No weekday, holiday, ticker, country or
    fixed-close inference happens here.

    A session is eligible only when its close is strictly before ``event_at``.
    Equality is not enough: a release timestamp exactly at the official close
    has no safe ordering proof that the completed daily bar preceded the event.
    The two reference sessions are therefore the last two sessions with
    ``session_close < event_at``.

    ``event_trading_date`` is still the first supplied session on or after the
    market-local event date. For a genuine post-close event,
    ``latest_closed_session`` may equal ``event_trading_date``.

    ``now`` is a separate acquisition-time gate. It does not change which pair
    ``event_at`` implies; it only proves that the selected sessions have
    actually closed by acquisition time so a still-forming daily candle can
    never be persisted.
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
            raise ValueError(f"session close for {session_date} must be timezone-aware")

    event_local_date = event_at.astimezone(ZoneInfo(profile.market_timezone)).date()
    event_index = next(
        (index for index, session_date in enumerate(session_dates) if session_date >= event_local_date),
        None,
    )
    if event_index is None:
        raise ValueError("confirmed session calendar does not reach the event trading date")

    eligible = [
        session_date for session_date, session_close in session_closes if session_close < event_at
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
