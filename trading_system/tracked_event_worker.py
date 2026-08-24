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
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_event_config import snapshot_effective_tracking_config
from trading_system.tracked_event_reaction_live import stream_tracked_event_reaction_runtime
from trading_system.tracked_event_reaction_runtime import TrackedEventReactionRuntime
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    SupabaseTrackedEventRepository,
    TrackedEventReactionRecord,
    TrackedEventStatus,
)
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument, resolve_tracked_instrument
from trading_system.tracked_instruments import TrackedInstrument, TrackedInstrumentSource


WORKER_ACTOR = "tracked-event-worker"
PREFLIGHT_ACTOR = "tracked-event-preflight"
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


def _normalise_text(value: str) -> str:
    return " ".join(value.strip().upper().split())


def _needs_resolution_preflight(event: PersistentTrackedEvent) -> bool:
    if event.resolved_etoro_instrument_id is None:
        return True
    if event.resolved_etoro_market:
        return False
    # Rows that already captured a reference, or already entered MONITORING,
    # predate broker-market persistence and cannot safely use the capture RPC.
    # Let those legacy runs resume with their already-persisted broker identity;
    # only new TRACKED rows before reference capture must backfill the market.
    return event.status == TrackedEventStatus.TRACKED and event.reference_price is None


def _resolved_identity_from_event(event: PersistentTrackedEvent) -> TrackedEtoroInstrument:
    if event.resolved_etoro_instrument_id is None or event.resolved_etoro_instrument_id <= 0:
        raise RuntimeError("tracked event is not armed with an eToro instrument id")
    if not event.resolved_etoro_symbol:
        raise RuntimeError("tracked event is not armed with an eToro symbol")
    if not event.resolved_etoro_display_name:
        raise RuntimeError("tracked event is not armed with an eToro display name")
    if event.resolution_armed_at is None or not event.resolution_armed_by:
        raise RuntimeError("tracked event eToro resolution is not armed")
    return TrackedEtoroInstrument(
        tracked_instrument_id=event.tracked_instrument_id,
        instrument=event.instrument,
        market=event.market,
        etoro_instrument_id=event.resolved_etoro_instrument_id,
        etoro_symbol=event.resolved_etoro_symbol,
        etoro_display_name=event.resolved_etoro_display_name,
        etoro_market=event.resolved_etoro_market or "",
    )


def _preflight_resolution_sync(
    event: PersistentTrackedEvent,
    *,
    repository: SupabaseTrackedEventRepository,
    provider: EtoroMarketDataProvider,
) -> PersistentTrackedEvent:
    """Resolve and validate broker identity outside the event critical path.

    This may traverse eToro's catalog and therefore can be slow. run_forever runs
    it in a background thread as soon as an event enters the lookahead window.
    monitor_one_event never calls the catalog resolver.
    """
    if not _needs_resolution_preflight(event):
        return event
    if datetime.now(UTC) >= event.event_at:
        raise RuntimeError("event reached event_at before eToro identity was fully armed")

    tracked = _tracked_identity(event)
    resolved = resolve_tracked_instrument(tracked, EtoroInstrumentResolver(provider))
    if resolved is None:
        raise RuntimeError("eToro instrument resolution failed or was ambiguous")

    # Preflight owns broker identity only. A closed exchange may legitimately
    # return an empty/non-positive lastExecution, so do not gate arming on price.
    # The timing-critical reference capture below still requires a finite,
    # positive pre-event lastExecution and therefore remains fail-closed.
    quote = provider.fetch_quote(resolved.etoro_instrument_id)
    if quote.instrument_id != resolved.etoro_instrument_id:
        raise RuntimeError("eToro preflight quote identity mismatch")

    if event.resolved_etoro_instrument_id is None:
        event = repository.arm_resolution(
            event_id=event.event_id,
            etoro_instrument_id=resolved.etoro_instrument_id,
            etoro_symbol=resolved.etoro_symbol,
            etoro_display_name=resolved.etoro_display_name,
            actor=PREFLIGHT_ACTOR,
        )

    return repository.capture_resolved_etoro_market(
        event_id=event.event_id,
        etoro_instrument_id=resolved.etoro_instrument_id,
        etoro_symbol=resolved.etoro_symbol,
        etoro_display_name=resolved.etoro_display_name,
        etoro_market=resolved.etoro_market,
        actor=PREFLIGHT_ACTOR,
    )


def _validate_persisted_reference_identity(
    event: PersistentTrackedEvent,
    resolved: TrackedEtoroInstrument,
) -> None:
    if event.reference_price is None:
        return
    if event.resolved_etoro_instrument_id != resolved.etoro_instrument_id:
        raise RuntimeError("persisted reference eToro instrument id does not match armed identity")
    if not event.resolved_etoro_symbol or _normalise_text(event.resolved_etoro_symbol) != _normalise_text(
        resolved.etoro_symbol
    ):
        raise RuntimeError("persisted reference eToro symbol does not match armed identity")
    if not event.resolved_etoro_display_name or _normalise_text(
        event.resolved_etoro_display_name
    ) != _normalise_text(resolved.etoro_display_name):
        raise RuntimeError("persisted reference eToro display name does not match armed identity")


def _capture_reference_if_needed(
    event: PersistentTrackedEvent,
    *,
    repository: SupabaseTrackedEventRepository,
    provider: EtoroMarketDataProvider,
    resolved: TrackedEtoroInstrument,
    now: datetime,
) -> PersistentTrackedEvent:
    if event.reference_price is not None:
        _validate_persisted_reference_identity(event, resolved)
        return event
    if now >= event.event_at:
        raise RuntimeError("event reached event_at without a persisted pre-event reference")

    quote = provider.fetch_quote(resolved.etoro_instrument_id)
    if quote.instrument_id != resolved.etoro_instrument_id:
        raise RuntimeError("eToro reference quote identity mismatch")
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
        _validate_persisted_reference_identity(event, resolved)
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


def _restore_reaction_pipeline(
    event: PersistentTrackedEvent,
    *,
    resolved: TrackedEtoroInstrument,
    baseline: EventMarketReactionBaseline,
    reaction_pipeline: EventMarketReactionPipeline,
    repository: SupabaseTrackedEventRepository,
) -> None:
    for record in repository.list_reactions(event.event_id):
        if record.tracked_market_event_id != event.event_id:
            raise RuntimeError("persisted reaction belongs to a different event")
        if record.reference_price != baseline.reference_price:
            raise RuntimeError("persisted reaction reference no longer matches event reference")
        replay_candle = TrackedMarketCandle(
            tracked_instrument_id=resolved.tracked_instrument_id,
            instrument=resolved.instrument,
            market=resolved.market,
            etoro_instrument_id=resolved.etoro_instrument_id,
            interval_minutes=record.interval_minutes,
            start=record.candle_start,
            open=record.close_price,
            high=record.close_price,
            low=record.close_price,
            close=record.close_price,
            source_minutes=record.interval_minutes,
        )
        replayed = reaction_pipeline.add(replay_candle, baseline=baseline)
        reaction = replayed.tracked_reaction.reaction
        evolution = replayed.tracked_reaction.evolution
        if (
            reaction.return_pct != record.return_pct
            or reaction.direction.value != record.direction
            or evolution.evolution.value != record.evolution
        ):
            raise RuntimeError("persisted reaction history cannot be restored deterministically")


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


def _capture_tracking_config_snapshot(
    event: PersistentTrackedEvent,
    *,
    repository: SupabaseTrackedEventRepository,
    monitor_hours: float,
    reference_lead_seconds: float,
    max_wait_for_market_hours: float,
) -> PersistentTrackedEvent:
    """Persist the exact effective settings this event is about to be monitored with.

    Built from the same resolved values (monitor_hours/reference_lead_seconds/
    max_wait_for_market_hours from run_forever's env resolution, and the reaction
    profile monitor_one_event actually uses below) rather than re-reading defaults,
    so the snapshot can never drift from what this run actually does.
    """
    snapshot = snapshot_effective_tracking_config(
        monitor_hours=monitor_hours,
        reference_lead_seconds=reference_lead_seconds,
        max_wait_for_market_hours=max_wait_for_market_hours,
        profile=DEFAULT_EVENT_REACTION_MONITORING_PROFILE,
    )
    return repository.capture_tracking_config_snapshot(
        event_id=event.event_id,
        snapshot=snapshot.to_dict(),
        actor=WORKER_ACTOR,
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
    # A pre-snapshot legacy run can already be in MONITORING after deployment.
    # Its earlier reactions may have been produced with settings we cannot
    # reconstruct, so never backfill today's settings and mislabel history.
    # New TRACKED events, and MONITORING restarts that already have a snapshot,
    # still go through the capture-once RPC so identical settings are verified
    # idempotently and conflicts fail closed before any new market-data work.
    legacy_in_progress = (
        event.status == TrackedEventStatus.MONITORING
        and event.tracking_config_snapshot is None
    )
    if not legacy_in_progress:
        try:
            event = _capture_tracking_config_snapshot(
                event,
                repository=repository,
                monitor_hours=monitor_hours,
                reference_lead_seconds=reference_lead_seconds,
                max_wait_for_market_hours=max_wait_for_market_hours,
            )
        except RuntimeError as exc:
            repository.mark_failed(event.event_id, actor=WORKER_ACTOR, error=str(exc))
            return

    try:
        resolved = _resolved_identity_from_event(event)
    except RuntimeError as exc:
        repository.mark_failed(event.event_id, actor=WORKER_ACTOR, error=str(exc))
        return

    try:
        event = await _ensure_reference(
            event,
            repository=repository,
            provider=provider,
            resolved=resolved,
            reference_lead_seconds=reference_lead_seconds,
        )
    except RuntimeError as exc:
        repository.mark_failed(event.event_id, actor=WORKER_ACTOR, error=str(exc))
        return
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
    try:
        _restore_reaction_pipeline(
            event,
            resolved=resolved,
            baseline=baseline,
            reaction_pipeline=reaction_pipeline,
            repository=repository,
        )
    except RuntimeError as exc:
        repository.mark_failed(event.event_id, actor=WORKER_ACTOR, error=str(exc))
        return

    runtime = TrackedEventReactionRuntime()
    stream = stream_tracked_event_reaction_runtime((resolved,), provider, runtime, reconnect=True)
    reaction_anchor_at = event.reaction_anchor_at

    try:
        if reaction_anchor_at is None:
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
    preflight: dict[str, asyncio.Task[PersistentTrackedEvent]] = {}
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

        for event_id, task in list(preflight.items()):
            if not task.done():
                continue
            preflight.pop(event_id, None)
            try:
                armed = task.result()
                print(
                    f"tracked-event preflight armed event={event_id} "
                    f"etoro_id={armed.resolved_etoro_instrument_id} "
                    f"symbol={armed.resolved_etoro_symbol}",
                    flush=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"tracked-event preflight retryable failure event={event_id}: {exc}", flush=True)

        now = datetime.now(UTC)
        try:
            runnable = repository.list_runnable(now=now, lookahead=lookahead, max_past=max_past)
        except Exception as exc:
            print(f"tracked-event list failure: {exc}", flush=True)
            await asyncio.sleep(poll_seconds)
            continue

        for event in runnable:
            if event.event_id in active or event.event_id in preflight:
                continue

            if _needs_resolution_preflight(event):
                if now >= event.event_at:
                    repository.mark_failed(
                        event.event_id,
                        actor=WORKER_ACTOR,
                        error="event reached event_at before eToro identity was fully armed",
                    )
                    continue
                # Keep catalog discovery outside the timing-critical monitor and
                # off the asyncio loop. Limit to one catalog traversal at a time
                # because the live eToro search contract can be expensive.
                if not preflight:
                    preflight[event.event_id] = asyncio.create_task(
                        asyncio.to_thread(
                            _preflight_resolution_sync,
                            event,
                            repository=repository,
                            provider=provider,
                        ),
                        name=f"tracked-event-preflight-{event.event_id}",
                    )
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
