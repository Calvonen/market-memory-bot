from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from market_memory.data import fetch_ohlcv
from trading_system.market_session_profile import (
    GROUNDED_MARKET_SESSION_PROFILES,
    MarketSessionProfile,
    resolve_market_session_profile,
    resolve_provider_symbol,
)
from trading_system.pre_event_market_context_acquisition import (
    DailyOhlcvFetcher,
    acquire_pre_event_market_context,
)
from trading_system.pre_event_market_context_persistence import capture_pre_event_market_context
from trading_system.session_calendar_adapter import confirmed_session_closes
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
    provider_symbol: str,
    event_trading_date: date,
    last_confirmed_closed_session_date: date,
    previous_confirmed_closed_session_date: date,
    market_timezone: str,
    actor: str,
    fetcher: DailyOhlcvFetcher = fetch_ohlcv,
) -> PersistentTrackedEvent:
    """Acquire and persist pre-event context from caller-grounded session inputs.

    This legacy/caller-grounded entrypoint has no exchange-calendar close proof
    and no event-version CAS input. It therefore supports only strictly earlier
    sessions. Same-day capture is reserved for the automatic canonical entrypoint
    below, which derives the close from the grounded exchange calendar and uses
    the validated CAS-bound persistence RPC.
    """
    event = repository.get(event_id)
    if event is None:
        raise RuntimeError(f"tracked event {event_id} was not found")

    normalized_ticker = _normalise_ticker(ticker)
    canonical_instrument = _normalise_ticker(event.instrument)
    if not normalized_ticker or normalized_ticker != canonical_instrument:
        raise ValueError("ticker does not match tracked event instrument")

    normalized_provider_symbol = _normalise_ticker(provider_symbol)
    if not normalized_provider_symbol:
        raise ValueError("provider_symbol is required")

    if event.event_at.tzinfo is None or event.event_at.utcoffset() is None:
        raise ValueError("event_at must be timezone-aware")
    try:
        event_local_date = event.event_at.astimezone(ZoneInfo(market_timezone)).date()
    except Exception as exc:
        raise ValueError("invalid market_timezone") from exc

    # The base persistence RPC derives the event-local date from the persisted
    # row's event_at. Mirror that authority here before any provider call so a
    # caller cannot bypass the no-same-day trust boundary by supplying a later
    # event_trading_date. Same-day capture requires the canonical validated path.
    if last_confirmed_closed_session_date >= event_local_date:
        raise ValueError(
            "caller-grounded pre-event context requires a session strictly before "
            "the persisted event local date; same-day capture requires the validated canonical path"
        )

    # Keep the caller's own trading-date contract as a secondary consistency
    # check, but it is not the authority for the same-day trust boundary.
    if last_confirmed_closed_session_date >= event_trading_date:
        raise ValueError(
            "caller-grounded pre-event context requires a session strictly before "
            "event_trading_date; same-day capture requires the validated canonical path"
        )

    context = acquire_pre_event_market_context(
        provider_symbol=normalized_provider_symbol,
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
        session_closes = confirmed_session_closes(profile, **calendar_kwargs)
    else:
        session_closes = confirmed_session_closes(
            profile, calendar_loader=calendar_loader, **calendar_kwargs
        )

    resolution = resolve_session_dates(
        event.event_at,
        profile=profile,
        session_closes=session_closes,
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
    """Resolve grounded market sessions, then acquire and persist event context."""
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
    provider_symbol = resolve_provider_symbol(canonical_instrument, profile=profile)
    event_local_date = event.event_at.astimezone(ZoneInfo(profile.market_timezone)).date()
    calendar_kwargs = {
        "start_date": event_local_date - timedelta(days=_SESSION_CALENDAR_LOOKBACK_DAYS),
        "end_date": event_local_date + timedelta(days=_SESSION_CALENDAR_LOOKAHEAD_DAYS),
    }
    if calendar_loader is None:
        session_closes = confirmed_session_closes(profile, **calendar_kwargs)
    else:
        session_closes = confirmed_session_closes(
            profile,
            calendar_loader=calendar_loader,
            **calendar_kwargs,
        )

    resolution = resolve_session_dates(
        event.event_at,
        profile=profile,
        session_closes=session_closes,
        now=datetime.now(UTC),
    )
    context = acquire_pre_event_market_context(
        provider_symbol=provider_symbol,
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
        session_close=dict(session_closes)[resolution.latest_closed_session],
    )
