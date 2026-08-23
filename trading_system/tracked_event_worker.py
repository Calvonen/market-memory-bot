from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.event_market_reaction import (
    EventMarketReactionBaseline,
    EventMarketReactionPipeline,
)
from trading_system.reaction_monitoring_profile import DEFAULT_EVENT_REACTION_MONITORING_PROFILE
from trading_system.tracked_event_reaction_live import stream_tracked_event_reaction_runtime
from trading_system.tracked_event_reaction_runtime import TrackedEventReactionRuntime
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    SupabaseTrackedEventRepository,
    TrackedEventReactionRecord,
)
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument, resolve_tracked_instrument
from trading_system.tracked_instruments import TrackedInstrument, TrackedInstrumentSource


WORKER_ACTOR = "tracked-event-worker"
DEFAULT_POLL_SECONDS = 30.0
DEFAULT_LOOKAHEAD_HOURS = 24.0
DEFAULT_MAX_PAST_HOURS = 12.0
DEFAULT_MONITOR_HOURS = 8.0


def _positive_float_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number")
    return value


def _tracking_source(source: str) -> TrackedInstrumentSource:
    if source == "calendar":
        return TrackedInstrumentSource.CALENDAR
    if source == "scanner":
        return TrackedInstrumentSource.SCANNER
    return TrackedInstrumentSource.MANUAL


def _tracked_identity(event: PersistentTrackedEvent) -> TrackedInstrument:
    return TrackedInstrument(
        instrument=event.instrument,
        company_name=event.company_name,
        market=event.market,
        sources=(_tracking_source(event.source),),
        active=True,
        tracked_instrument_id=event.tracked_instrument_id,
        created_at=event.created_at or datetime.now(UTC),
        updated_at=event.updated_at or datetime.now(UTC),
    )


def _capture_reference_if_needed(
    event: PersistentTrackedEvent,
    *,
    repository: SupabaseTrackedEventRepository,
    provider: EtoroMarketDataProvider,
    resolved: TrackedEtoroInstrument,
    now: datetime,
) -> PersistentTrackedEvent:
    if event.reference_price is not None:
        return event
    if now >= event.event_at:
        raise RuntimeError("event reached event_at without a persisted pre-event reference")

    quote = provider.fetch_quote(resolved.etoro_instrument_id)
    price = quote.last_execution
    if not price.is_finite() or price <= 0:
        raise RuntimeError("eToro pre-event lastExecution is not finite and positive")

    # The quote timestamp is the timestamp of the market observation itself.
    # For an after-hours event this may point to the previous session's final
    # execution, which is exactly the intended overnight baseline. If eToro
    # omits a timestamp, the fetch time is used, but only while it is strictly
    # before event_at. Never backdate a post-event fetch to make it look valid.
    captured_at = quote.timestamp or now
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        captured_at = captured_at.replace(tzinfo=UTC)
    captured_at = captured_at.astimezone(UTC)
    if captured_at > event.event_at:
        raise RuntimeError("eToro reference timestamp is after event_at")

    return repository.capture_reference(
        event_id=event.event_id,
        reference_price=price,
        captured_at=captured_at,
        reference_kind="etoro_last_execution_pre_event_snapshot",
        etoro_instrument_id=resolved.etoro_instrument_id,
        etoro_symbol=resolved.etoro_symbol,
        etoro_display_name=resolved.etoro_display_name,
        actor=WORKER_ACTOR,
    )


async def monitor_one_event(
    event: PersistentTrackedEvent,
    *,
    repository: SupabaseTrackedEventRepository,
    provider: EtoroMarketDataProvider,
    monitor_hours: float = DEFAULT_MONITOR_HOURS,
) -> None:
    tracked = _tracked_identity(event)
    resolved = resolve_tracked_instrument(tracked, EtoroInstrumentResolver(provider))
    if resolved is None:
        repository.mark_failed(
            event.event_id,
            actor=WORKER_ACTOR,
            error="eToro instrument resolution failed or was ambiguous",
        )
        return

    now = datetime.now(UTC)
    try:
        event = _capture_reference_if_needed(
            event,
            repository=repository,
            provider=provider,
            resolved=resolved,
            now=now,
        )
    except RuntimeError as exc:
        if now >= event.event_at:
            repository.mark_failed(event.event_id, actor=WORKER_ACTOR, error=str(exc))
            return
        # Network/provider failures before the event stay retryable: leave the
        # event tracked so the outer poll loop can try again instead of turning
        # a temporary outage into a permanent failed event.
        raise

    if event.reference_price is None:
        raise RuntimeError("reference capture returned without a reference price")

    delay = (event.event_at - datetime.now(UTC)).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)

    repository.mark_monitoring(event.event_id, actor=WORKER_ACTOR, started_at=datetime.now(UTC))

    baseline = EventMarketReactionBaseline(
        event_id=event.event_id,
        tracked_instrument_id=resolved.tracked_instrument_id,
        instrument=resolved.instrument,
        market=resolved.market,
        etoro_instrument_id=resolved.etoro_instrument_id,
        reference_price=event.reference_price,
    )
    reaction_pipeline = EventMarketReactionPipeline()
    runtime = TrackedEventReactionRuntime()
    stream = stream_tracked_event_reaction_runtime((resolved,), provider, runtime, reconnect=True)
    deadline = event.event_at + timedelta(hours=monitor_hours)
    remaining = max(0.0, (deadline - datetime.now(UTC)).total_seconds())
    if remaining <= 0:
        repository.mark_completed(event.event_id, actor=WORKER_ACTOR, completed_at=datetime.now(UTC))
        await stream.aclose()
        return

    try:
        async with asyncio.timeout(remaining):
            async for batch in stream:
                for candle in batch.candles:
                    if candle.start < event.event_at:
                        continue
                    observed_at = candle.start + timedelta(minutes=candle.interval_minutes)
                    active_interval = DEFAULT_EVENT_REACTION_MONITORING_PROFILE.interval_for(
                        event_at=event.event_at,
                        observed_at=observed_at,
                    )
                    if active_interval is None or candle.interval_minutes != active_interval:
                        continue
                    if candle.source_minutes != candle.interval_minutes:
                        continue

                    event_reaction = reaction_pipeline.add(candle, baseline=baseline)
                    reaction = event_reaction.tracked_reaction.reaction
                    evolution = event_reaction.tracked_reaction.evolution
                    repository.save_reaction(
                        TrackedEventReactionRecord(
                            tracked_market_event_id=event.event_id,
                            interval_minutes=reaction.interval_minutes,
                            candle_start=reaction.candle_start,
                            reference_price=reaction.reference_price,
                            close_price=reaction.close_price,
                            return_pct=reaction.return_pct,
                            direction=reaction.direction.value,
                            evolution=evolution.evolution.value,
                            observed_at=observed_at,
                        )
                    )
    except TimeoutError:
        pass
    finally:
        await stream.aclose()

    repository.mark_completed(event.event_id, actor=WORKER_ACTOR, completed_at=datetime.now(UTC))


async def run_forever() -> None:
    repository = SupabaseTrackedEventRepository.from_env()
    provider = EtoroMarketDataProvider.from_env()
    poll_seconds = _positive_float_from_env("MARKETAI_TRACKED_EVENT_POLL_SECONDS", DEFAULT_POLL_SECONDS)
    lookahead = timedelta(
        hours=_positive_float_from_env("MARKETAI_TRACKED_EVENT_LOOKAHEAD_HOURS", DEFAULT_LOOKAHEAD_HOURS)
    )
    max_past = timedelta(
        hours=_positive_float_from_env("MARKETAI_TRACKED_EVENT_MAX_PAST_HOURS", DEFAULT_MAX_PAST_HOURS)
    )
    monitor_hours = _positive_float_from_env("MARKETAI_TRACKED_EVENT_MONITOR_HOURS", DEFAULT_MONITOR_HOURS)

    active: dict[str, asyncio.Task[None]] = {}
    while True:
        for event_id, task in list(active.items()):
            if not task.done():
                continue
            active.pop(event_id, None)
            try:
                task.result()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"tracked-event worker retryable failure event={event_id}: {exc}", flush=True)

        now = datetime.now(UTC)
        try:
            runnable = repository.list_runnable(now=now, lookahead=lookahead, max_past=max_past)
        except Exception as exc:
            print(f"tracked-event list failure: {exc}", flush=True)
            await asyncio.sleep(poll_seconds)
            continue

        for event in runnable:
            if event.event_id in active:
                continue
            active[event.event_id] = asyncio.create_task(
                monitor_one_event(
                    event,
                    repository=repository,
                    provider=provider,
                    monitor_hours=monitor_hours,
                ),
                name=f"tracked-event-{event.event_id}",
            )

        await asyncio.sleep(poll_seconds)


def main() -> int:
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
