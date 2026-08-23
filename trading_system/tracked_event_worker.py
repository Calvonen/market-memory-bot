from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

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
DEFAULT_REFERENCE_LEAD_SECONDS = 30.0
DEFAULT_MAX_WAIT_FOR_MARKET_HOURS = 72.0
REFERENCE_RETRY_SECONDS = 5.0


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


async def _ensure_reference(
    event: PersistentTrackedEvent,
    *,
    repository: SupabaseTrackedEventRepository,
    provider: EtoroMarketDataProvider,
    resolved: TrackedEtoroInstrument,
    reference_lead_seconds: float,
) -> PersistentTrackedEvent | None:
    if event.reference_price is not None:
        return event

    target = event.event_at - timedelta(seconds=reference_lead_seconds)
    delay = (target - datetime.now(UTC)).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)

    last_error: Exception | None = None
    while datetime.now(UTC) < event.event_at:
        now = datetime.now(UTC)
        try:
            return _capture_reference_if_needed(
                event,
                repository=repository,
                provider=provider,
                resolved=resolved,
                now=now,
            )
        except RuntimeError as exc:
            last_error = exc
            remaining = (event.event_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(min(REFERENCE_RETRY_SECONDS, remaining))

    repository.mark_failed(
        event.event_id,
        actor=WORKER_ACTOR,
        error=str(last_error or "event reached event_at without a persisted pre-event reference"),
    )
    return None


def _persist_reactions_from_batch(
    event: PersistentTrackedEvent,
    *,
    batch,
    baseline: EventMarketReactionBaseline,
    reaction_pipeline: EventMarketReactionPipeline,
    repository: SupabaseTrackedEventRepository,
    reaction_anchor_at: datetime,
) -> None:
    for candle in batch.candles:
        if candle.start < event.event_at:
            continue
        observed_at = candle.start + timedelta(minutes=candle.interval_minutes)
        active_interval = DEFAULT_EVENT_REACTION_MONITORING_PROFILE.interval_for(
            event_at=reaction_anchor_at,
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


async def monitor_one_event(
    event: PersistentTrackedEvent,
    *,
    repository: SupabaseTrackedEventRepository,
    provider: EtoroMarketDataProvider,
    monitor_hours: float = DEFAULT_MONITOR_HOURS,
    reference_lead_seconds: float = DEFAULT_REFERENCE_LEAD_SECONDS,
    max_wait_for_market_hours: float = DEFAULT_MAX_WAIT_FOR_MARKET_HOURS,
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

    event = await _ensure_reference(
        event,
        repository=repository,
        provider=provider,
        resolved=resolved,
        reference_lead_seconds=reference_lead_seconds,
    )
    if event is None:
        return
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
    reaction_anchor_at = event.reaction_anchor_at

    try:
        if reaction_anchor_at is None:
            # Wait for the first *real* complete post-event 1m candle. This is
            # the reaction-stage anchor. For an overnight event the exchange may
            # be closed for hours after event_at, so the 1m -> 5m -> 15m profile
            # must not advance while no tradable candles exist.
            try:
                async with asyncio.timeout(max_wait_for_market_hours * 3600):
                    async for batch in stream:
                        first_minutes = [
                            candle
                            for candle in batch.candles
                            if candle.interval_minutes == 1
                            and candle.source_minutes == 1
                            and candle.start >= event.event_at
                        ]
                        if not first_minutes:
                            continue
                        reaction_anchor_at = min(candle.start for candle in first_minutes)
                        event = repository.capture_reaction_anchor(
                            event_id=event.event_id,
                            reaction_anchor_at=reaction_anchor_at,
                            actor=WORKER_ACTOR,
                        )
                        _persist_reactions_from_batch(
                            event,
                            batch=batch,
                            baseline=baseline,
                            reaction_pipeline=reaction_pipeline,
                            repository=repository,
                            reaction_anchor_at=reaction_anchor_at,
                        )
                        break
            except TimeoutError:
                repository.mark_failed(
                    event.event_id,
                    actor=WORKER_ACTOR,
                    error="no complete post-event 1m market candle within wait horizon",
                )
                return

        if reaction_anchor_at is None:
            raise RuntimeError("reaction anchor capture returned without reaction_anchor_at")

        remaining = max(
            0.0,
            ((reaction_anchor_at + timedelta(hours=monitor_hours)) - datetime.now(UTC)).total_seconds(),
        )
        if remaining <= 0:
            repository.mark_completed(event.event_id, actor=WORKER_ACTOR, completed_at=datetime.now(UTC))
            return

        try:
            async with asyncio.timeout(remaining):
                async for batch in stream:
                    _persist_reactions_from_batch(
                        event,
                        batch=batch,
                        baseline=baseline,
                        reaction_pipeline=reaction_pipeline,
                        repository=repository,
                        reaction_anchor_at=reaction_anchor_at,
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
    reference_lead_seconds = _positive_float_from_env(
        "MARKETAI_TRACKED_EVENT_REFERENCE_LEAD_SECONDS", DEFAULT_REFERENCE_LEAD_SECONDS
    )
    max_wait_for_market_hours = _positive_float_from_env(
        "MARKETAI_TRACKED_EVENT_MAX_WAIT_FOR_MARKET_HOURS", DEFAULT_MAX_WAIT_FOR_MARKET_HOURS
    )

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
                    reference_lead_seconds=reference_lead_seconds,
                    max_wait_for_market_hours=max_wait_for_market_hours,
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
