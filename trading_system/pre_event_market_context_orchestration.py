from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from market_memory.data import fetch_ohlcv
from trading_system.market_session_profile import (
    GROUNDED_MARKET_SESSION_PROFILES,
    MarketSessionProfile,
    resolve_market_session_profile,
)
from trading_system.pre_event_market_context_acquisition import (
    DailyOhlcvFetcher,
    acquire_pre_event_market_context,
)
from trading_system.pre_event_market_context_persistence import capture_pre_event_market_context
from trading_system.session_calendar_adapter import confirmed_session_dates
from trading_system.session_date_resolver import resolve_session_dates
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    SupabaseTrackedEventRepository,
)


_SESSION_CALENDAR_LOOKBACK_DAYS = 31
_SESSION_CALENDAR_LOOKAHEAD_DAYS = 31


def _normalise_ticker(value: str) -> str:
    return value.strip().upper()


def acquire_and_persist_pre_event_market_context(
    repository: SupabaseTrackedEventRepository,
    *,
    event_id: str,
    ticker: str,
    event_trading_date: date,
    last_confirmed_closed_session_date: date,
    previous_confirmed_closed_session_date: date,
    market_timezone: str,
    actor: str,
    fetcher: DailyOhlcvFetcher = fetch_ohlcv,
) -> PersistentTrackedEvent:
    """Acquire and persist pre-event context from caller-grounded session inputs.

    The caller remains responsible for resolving the market timezone, event
    trading date, and the two confirmed closed session dates. This orchestration
    layer deliberately performs no exchange/calendar/session inference. Before
    acquisition, the supplied ticker is bound to the canonical persisted event
    instrument so another instrument's prices cannot be captured by mistake.
    """
    event = repository.get(event_id)
    if event is None:
        raise RuntimeError(f"tracked event {event_id} was not found")

    normalized_ticker = _normalise_ticker(ticker)
    canonical_instrument = _normalise_ticker(event.instrument)
    if not normalized_ticker or normalized_ticker != canonical_instrument:
        raise ValueError("ticker does not match tracked event instrument")

    context = acquire_pre_event_market_context(
        ticker=normalized_ticker,
        event_trading_date=event_trading_date,
        last_confirmed_closed_session_date=last_confirmed_closed_session_date,
        previous_confirmed_closed_session_date=previous_confirmed_closed_session_date,
        fetcher=fetcher,
    )
    return capture_pre_event_market_context(
        repository,
        event_id=event_id,
        snapshot=context.to_dict(),
        market_timezone=market_timezone,
        actor=actor,
    )


def persisted_pre_event_market_context_is_current(
    event: PersistentTrackedEvent,
    *,
    profiles: tuple[MarketSessionProfile, ...] = GROUNDED_MARKET_SESSION_PROFILES,
    calendar_loader: Any | None = None,
) -> bool:
    """Revalidate a persisted pre-event context against the event's current event_at.

    ``pre_event_market_context`` is immutable once captured, but ``event_at`` can
    still be edited afterwards (see ``upsert_tracked_market_event``) while the
    event stays TRACKED with no reference yet. The persisted snapshot's
    ``session_date``/``previous_session_date`` are the exact two closed sessions
    it was grounded on; this recomputes that same pair from the event's current
    ``event_at`` using only calendar/session logic - no Yahoo fetch, no new
    persistence write - and checks they still match. A mismatch means the
    snapshot no longer corresponds to the sessions before this event's current
    trading date and must not be reused for monitoring.
    """
    snapshot = event.pre_event_market_context
    if snapshot is None:
        raise ValueError("tracked event has no persisted pre_event_market_context")
    if event.event_at.tzinfo is None or event.event_at.utcoffset() is None:
        raise ValueError("event_at must be timezone-aware")
    if not event.resolved_etoro_market:
        raise ValueError("tracked event has no resolved_etoro_market")

    try:
        snapshot_session_date = date.fromisoformat(str(snapshot["session_date"]))
        snapshot_previous_session_date = date.fromisoformat(str(snapshot["previous_session_date"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("persisted pre_event_market_context is missing session-date metadata") from exc

    profile = resolve_market_session_profile(event.resolved_etoro_market, profiles=profiles)
    event_local_date = event.event_at.astimezone(ZoneInfo(profile.market_timezone)).date()
    calendar_kwargs = {
        "start_date": event_local_date - timedelta(days=_SESSION_CALENDAR_LOOKBACK_DAYS),
        "end_date": event_local_date + timedelta(days=_SESSION_CALENDAR_LOOKAHEAD_DAYS),
    }
    if calendar_loader is None:
        sessions = confirmed_session_dates(profile, **calendar_kwargs)
    else:
        sessions = confirmed_session_dates(profile, calendar_loader=calendar_loader, **calendar_kwargs)

    resolution = resolve_session_dates(
        event.event_at,
        profile=profile,
        confirmed_session_dates=sessions,
    )
    return (
        resolution.latest_closed_session == snapshot_session_date
        and resolution.previous_closed_session == snapshot_previous_session_date
    )


def acquire_and_persist_pre_event_market_context_for_event(
    repository: SupabaseTrackedEventRepository,
    *,
    event_id: str,
    ticker: str,
    actor: str,
    fetcher: DailyOhlcvFetcher = fetch_ohlcv,
    profiles: tuple[MarketSessionProfile, ...] = GROUNDED_MARKET_SESSION_PROFILES,
    calendar_loader: Any | None = None,
) -> PersistentTrackedEvent:
    """Resolve grounded market sessions, then acquire and persist event context.

    The persisted exact ``resolved_etoro_market`` label selects one explicitly
    grounded market-session profile. The profile calendar supplies confirmed
    exchange sessions and ``resolve_session_dates`` selects the event trading
    date plus the two immediately preceding sessions. The final persistence is
    compare-and-swap bound to the same tracked-event ``updated_at`` version that
    supplied event_at, instrument and broker market, so concurrent event edits
    fail closed rather than locking a stale immutable context.
    """
    event = repository.get(event_id)
    if event is None:
        raise RuntimeError(f"tracked event {event_id} was not found")
    if event.event_at.tzinfo is None or event.event_at.utcoffset() is None:
        raise ValueError("event_at must be timezone-aware")
    if event.updated_at is None:
        raise ValueError("tracked event has no updated_at version")
    if event.updated_at.tzinfo is None or event.updated_at.utcoffset() is None:
        raise ValueError("tracked event updated_at must be timezone-aware")
    if not event.resolved_etoro_market:
        raise ValueError("tracked event has no resolved_etoro_market")

    normalized_ticker = _normalise_ticker(ticker)
    canonical_instrument = _normalise_ticker(event.instrument)
    if not normalized_ticker or normalized_ticker != canonical_instrument:
        raise ValueError("ticker does not match tracked event instrument")

    profile = resolve_market_session_profile(
        event.resolved_etoro_market,
        profiles=profiles,
    )
    event_local_date = event.event_at.astimezone(ZoneInfo(profile.market_timezone)).date()
    calendar_kwargs = {
        "start_date": event_local_date - timedelta(days=_SESSION_CALENDAR_LOOKBACK_DAYS),
        "end_date": event_local_date + timedelta(days=_SESSION_CALENDAR_LOOKAHEAD_DAYS),
    }
    if calendar_loader is None:
        sessions = confirmed_session_dates(profile, **calendar_kwargs)
    else:
        sessions = confirmed_session_dates(
            profile,
            calendar_loader=calendar_loader,
            **calendar_kwargs,
        )

    resolution = resolve_session_dates(
        event.event_at,
        profile=profile,
        confirmed_session_dates=sessions,
    )
    context = acquire_pre_event_market_context(
        ticker=normalized_ticker,
        event_trading_date=resolution.event_trading_date,
        last_confirmed_closed_session_date=resolution.latest_closed_session,
        previous_confirmed_closed_session_date=resolution.previous_closed_session,
        fetcher=fetcher,
    )
    return capture_pre_event_market_context(
        repository,
        event_id=event_id,
        snapshot=context.to_dict(),
        market_timezone=profile.market_timezone,
        actor=actor,
        expected_event_updated_at=event.updated_at,
    )
