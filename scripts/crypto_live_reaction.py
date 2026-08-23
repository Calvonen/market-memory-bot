from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.manual_market_event_ingress import register_manual_market_event
from trading_system.market_event import MarketEventKind
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.tracked_event_reaction_live import stream_tracked_event_reaction_runtime
from trading_system.tracked_event_reaction_runtime import TrackedEventReactionRuntime
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument, resolve_tracked_instrument
from trading_system.tracked_instruments import TrackedInstrumentSource, create_tracked_instrument


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one real eToro crypto stream through the tracked candle + event reaction runtime. "
            "The runner waits for a closed 1m candle, registers a manual event, then prints live reactions."
        )
    )
    parser.add_argument("--instrument", default="BTC", help="Tracked/eToro symbol to resolve (default: BTC)")
    parser.add_argument("--company-name", default="", help="Optional exact eToro display name constraint")
    parser.add_argument("--market", default="", help="Optional tracked market label")
    parser.add_argument(
        "--etoro-instrument-id",
        type=int,
        default=None,
        help=(
            "Optional exact eToro instrument id for an explicit live smoke test. "
            "When supplied, bypasses instrument search/resolution without changing production resolver behavior."
        ),
    )
    parser.add_argument("--observations", type=int, default=3, help="Stop after this many reaction observations")
    parser.add_argument("--timeout-seconds", type=float, default=900.0, help="Overall runner timeout")
    return parser.parse_args()


def _print_resolution_failure(provider: EtoroMarketDataProvider, instrument: str) -> None:
    print(f"RESOLVE FAILED: {instrument!r}")
    candidates = provider.search_instruments(instrument)
    if not candidates:
        print("SEARCH: no eToro candidates")
        return
    print("SEARCH CANDIDATES:")
    for candidate in candidates:
        print(
            "  "
            f"id={candidate.instrument_id} symbol={candidate.symbol!r} "
            f"name={candidate.display_name!r} market={candidate.market!r}"
        )


def _resolve_target(
    args: argparse.Namespace,
    provider: EtoroMarketDataProvider,
) -> TrackedEtoroInstrument | None:
    tracked = create_tracked_instrument(
        instrument=args.instrument,
        company_name=args.company_name,
        market=args.market,
        source=TrackedInstrumentSource.MANUAL,
    )
    if args.etoro_instrument_id is not None:
        if args.etoro_instrument_id <= 0:
            raise ValueError("--etoro-instrument-id must be positive")
        print(
            "RESOLVE BYPASS: "
            f"using explicit eToro instrument id {args.etoro_instrument_id}; "
            "production resolver behavior is unchanged"
        )
        return TrackedEtoroInstrument(
            tracked_instrument_id=tracked.tracked_instrument_id,
            instrument=tracked.instrument,
            market=tracked.market,
            etoro_instrument_id=args.etoro_instrument_id,
            etoro_symbol=tracked.instrument,
            etoro_display_name=args.company_name.strip() or tracked.instrument,
        )

    resolved = resolve_tracked_instrument(tracked, EtoroInstrumentResolver(provider))
    if resolved is None:
        _print_resolution_failure(provider, tracked.instrument)
    return resolved


async def _run(args: argparse.Namespace) -> int:
    if args.observations < 1:
        raise ValueError("--observations must be at least 1")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be positive")

    provider = EtoroMarketDataProvider.from_env()
    resolved = _resolve_target(args, provider)
    if resolved is None:
        return 2

    quote = provider.fetch_quote(resolved.etoro_instrument_id)
    print(
        "RESOLVED: "
        f"tracked={resolved.instrument} market={resolved.market!r} "
        f"etoro_id={resolved.etoro_instrument_id} symbol={resolved.etoro_symbol!r} "
        f"name={resolved.etoro_display_name!r}"
    )
    print(
        "QUOTE: "
        f"bid={quote.bid} ask={quote.ask} last={quote.last_execution} timestamp={quote.timestamp}"
    )

    runtime = TrackedEventReactionRuntime()
    monitor = RegisteredMarketEventMonitor(runtime)
    stream = stream_tracked_event_reaction_runtime((resolved,), provider, runtime, reconnect=True)

    event = None
    observation_count = 0
    print("STREAM: waiting for first closed 1m candle before registering the manual event...")

    try:
        async with asyncio.timeout(args.timeout_seconds):
            async for batch in stream:
                if batch.candles:
                    for candle in batch.candles:
                        print(
                            "CANDLE: "
                            f"{candle.interval_minutes}m start={candle.start.isoformat()} "
                            f"o={candle.open} h={candle.high} l={candle.low} c={candle.close} "
                            f"source_minutes={candle.source_minutes}"
                        )

                if event is None:
                    one_minute = [candle for candle in batch.candles if candle.interval_minutes == 1]
                    if not one_minute:
                        continue
                    event_at = datetime.now(UTC)
                    event = register_manual_market_event(
                        monitor,
                        resolved,
                        event_id=f"crypto-live-{uuid4().hex[:12]}",
                        event_at=event_at,
                        kind=MarketEventKind.CUSTOM,
                        title=f"Live crypto reaction test for {resolved.instrument}",
                    )
                    print(
                        "EVENT: "
                        f"registered id={event.event_id} event_at={event.event_at.isoformat()} "
                        f"reference will use the latest fully closed pre-event 1m candle"
                    )
                    continue

                observed_at = batch.update.update.timestamp or datetime.now(UTC)
                reactions = monitor.observe_batch(batch, observed_at=observed_at)
                for registered in reactions:
                    reaction = registered.observation.reaction.tracked_reaction.reaction
                    evolution = registered.observation.reaction.tracked_reaction.evolution
                    print(
                        "REACTION: "
                        f"event={registered.event.event_id} interval={reaction.interval_minutes}m "
                        f"candle_start={reaction.candle_start.isoformat()} "
                        f"reference={reaction.reference_price} close={reaction.close_price} "
                        f"return={reaction.return_pct}% direction={reaction.direction.value} "
                        f"evolution={evolution.evolution.value}"
                    )
                    observation_count += 1
                    if observation_count >= args.observations:
                        print(f"DONE: collected {observation_count} reaction observations")
                        return 0
    except TimeoutError:
        print(
            "TIMEOUT: "
            f"collected {observation_count}/{args.observations} reaction observations "
            f"within {args.timeout_seconds:g}s"
        )
        return 3
    finally:
        await stream.aclose()

    return 3


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
