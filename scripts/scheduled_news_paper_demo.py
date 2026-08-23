from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trading_system.brokers.etoro_demo import EtoroDemoBroker
from trading_system.etoro_instrument_resolver import EtoroInstrumentResolver
from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.models import (
    ComponentAssessment,
    Direction,
    PortfolioState,
    StrategyInputs,
    TradeLevels,
)
from trading_system.news_market_event_ingress import register_news_market_event
from trading_system.pipeline import PaperTradingPipeline
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.tracked_event_reaction_live import stream_tracked_event_reaction_runtime
from trading_system.tracked_event_reaction_runtime import TrackedEventReactionRuntime
from trading_system.tracked_instrument_etoro import resolve_tracked_instrument
from trading_system.tracked_instruments import TrackedInstrumentSource, create_tracked_instrument


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a scheduled NEWS event against real eToro market data and, after StrategyEngine + RiskEngine approval, "
            "submit a market order to eToro's DEMO / Virtual Portfolio only."
        )
    )
    parser.add_argument("--instrument", default="BTC")
    parser.add_argument("--company-name", default="")
    parser.add_argument("--market", default="")
    parser.add_argument(
        "--event-at",
        required=True,
        help="Aware ISO-8601 timestamp for the scheduled news event, e.g. 2026-08-23T11:00:00+03:00",
    )
    parser.add_argument("--title", default="Scheduled demo news event")
    parser.add_argument(
        "--trigger-pct",
        type=float,
        default=0.005,
        help="Absolute post-event return %% needed for the demo signal (default: 0.005%%)",
    )
    parser.add_argument("--demo-order-amount-usd", type=float, default=500.0)
    parser.add_argument("--paper-equity", type=float, default=500000.0)
    parser.add_argument(
        "--demo-volatility-pct",
        type=float,
        default=1.0,
        help="Explicit demo-only volatility input supplied to RiskEngine (default: 1.0%%)",
    )
    parser.add_argument("--max-observations", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    return parser.parse_args()


def _parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--event-at must include a timezone offset")
    return parsed.astimezone(UTC)


def _demo_strategy_inputs(*, instrument: str, event_id: str, direction: Direction) -> StrategyInputs:
    # Deliberately synthetic scoring: the demo exercises the existing strategy/risk/execution gates,
    # not real news/fundamental analysis. 25+20+10+10 = 65, above the default 60 confidence gate.
    return StrategyInputs(
        instrument=instrument,
        fundamental=ComponentAssessment("fundamental", Direction.NO_TRADE, 0, 35, ("demo: no fundamental claim",)),
        catalyst=ComponentAssessment("catalyst", direction, 25, 25, ("demo: scheduled news catalyst",)),
        technical=ComponentAssessment("technical", direction, 20, 20, ("live post-event move crossed demo trigger",)),
        market_memory=ComponentAssessment("market_memory", direction, 10, 10, ("demo evidence allocation",)),
        news_sentiment=ComponentAssessment("news_sentiment", direction, 10, 10, ("demo polarity follows observed market move",)),
        source_event_id=event_id,
        invalidation=("demo-only signal; not investment advice",),
    )


def _trade_levels(entry: float, direction: Direction) -> TradeLevels:
    stop_distance = 0.0025  # 0.25%
    target_distance = 0.0050  # 0.50% => 2.0 reward/risk
    if direction is Direction.LONG:
        return TradeLevels(
            entry=entry,
            stop=entry * (1.0 - stop_distance),
            target_1=entry * (1.0 + target_distance),
            target_2=entry * (1.0 + 0.0075),
        )
    return TradeLevels(
        entry=entry,
        stop=entry * (1.0 + stop_distance),
        target_1=entry * (1.0 - target_distance),
        target_2=entry * (1.0 - 0.0075),
    )


async def _run(args: argparse.Namespace) -> int:
    if args.trigger_pct < 0:
        raise ValueError("--trigger-pct must be non-negative")
    if args.demo_order_amount_usd <= 0:
        raise ValueError("--demo-order-amount-usd must be positive")
    if args.paper_equity <= 0:
        raise ValueError("--paper-equity must be positive")
    if args.demo_volatility_pct < 0:
        raise ValueError("--demo-volatility-pct must be non-negative")
    if args.max_observations < 1:
        raise ValueError("--max-observations must be at least 1")

    event_at = _parse_aware_datetime(args.event_at)
    provider = EtoroMarketDataProvider.from_env()
    tracked = create_tracked_instrument(
        instrument=args.instrument,
        company_name=args.company_name,
        market=args.market,
        source=TrackedInstrumentSource.MANUAL,
    )
    resolved = resolve_tracked_instrument(tracked, EtoroInstrumentResolver(provider))
    if resolved is None:
        print(f"RESOLVE FAILED: {tracked.instrument}")
        return 2

    quote = provider.fetch_quote(resolved.etoro_instrument_id)
    mid = (quote.bid + quote.ask) / Decimal("2")
    spread_pct = float(((quote.ask - quote.bid) / mid) * Decimal("100")) if mid > 0 else 999.0

    demo_broker = EtoroDemoBroker.from_env(
        instrument_id=resolved.etoro_instrument_id,
        amount_usd=args.demo_order_amount_usd,
    )
    demo_broker.verify_demo_access()

    print(
        "RESOLVED: "
        f"tracked={resolved.instrument} market={resolved.market!r} etoro_id={resolved.etoro_instrument_id} "
        f"symbol={resolved.etoro_symbol!r} name={resolved.etoro_display_name!r}"
    )
    print("ETORO DEMO ACCESS: ok (Virtual Portfolio readable)")
    print(
        "DEMO CONFIG: "
        f"event_at={event_at.isoformat()} trigger={args.trigger_pct}% demo_order_amount_usd={args.demo_order_amount_usd:g} "
        f"paper_equity={args.paper_equity:g} spread={spread_pct:.6f}% "
        f"demo_volatility={args.demo_volatility_pct}% ETORO_DEMO_ONLY=true"
    )

    runtime = TrackedEventReactionRuntime()
    monitor = RegisteredMarketEventMonitor(runtime)
    stream = stream_tracked_event_reaction_runtime((resolved,), provider, runtime, reconnect=True)
    pipeline = PaperTradingPipeline(broker=demo_broker)

    event = None
    observation_count = 0

    try:
        async with asyncio.timeout(args.timeout_seconds):
            async for batch in stream:
                for candle in batch.candles:
                    print(
                        "CANDLE: "
                        f"{candle.interval_minutes}m start={candle.start.isoformat()} "
                        f"o={candle.open} h={candle.high} l={candle.low} c={candle.close}"
                    )

                if event is None:
                    pre_event_one_minute = [
                        candle
                        for candle in batch.candles
                        if candle.interval_minutes == 1
                        and candle.start + timedelta(minutes=1) <= event_at
                    ]
                    if not pre_event_one_minute:
                        continue
                    event = register_news_market_event(
                        monitor,
                        resolved,
                        event_id=f"scheduled-news-demo-{uuid4().hex[:12]}",
                        event_at=event_at,
                        title=args.title,
                    )
                    print(
                        "EVENT REGISTERED: "
                        f"id={event.event_id} source=news event_at={event.event_at.isoformat()} "
                        "waiting for clean post-event candle"
                    )
                    continue

                observed_at = batch.update.update.timestamp or datetime.now(UTC)
                reactions = monitor.observe_batch(batch, observed_at=observed_at)
                for registered in reactions:
                    reaction = registered.observation.reaction.tracked_reaction.reaction
                    observation_count += 1
                    print(
                        "REACTION: "
                        f"#{observation_count} start={reaction.candle_start.isoformat()} "
                        f"reference={reaction.reference_price} close={reaction.close_price} "
                        f"return={reaction.return_pct}%"
                    )

                    move = float(reaction.return_pct)
                    if abs(move) < args.trigger_pct:
                        if observation_count >= args.max_observations:
                            print("DONE: demo trigger was not crossed; no eToro demo order")
                            return 4
                        continue

                    direction = Direction.LONG if move > 0 else Direction.SHORT
                    entry = float(reaction.close_price)
                    result = pipeline.run(
                        _demo_strategy_inputs(
                            instrument=resolved.instrument,
                            event_id=event.event_id,
                            direction=direction,
                        ),
                        _trade_levels(entry, direction),
                        PortfolioState(
                            equity=args.paper_equity,
                            cash=args.paper_equity,
                            open_positions=0,
                            instrument_exposure_pct=0.0,
                            daily_pnl=0.0,
                            spread_pct=spread_pct,
                            volatility_pct=args.demo_volatility_pct,
                        ),
                    )
                    print(
                        "STRATEGY: "
                        f"direction={result.strategy.direction.value} confidence={result.strategy.confidence} "
                        f"long={result.strategy.long_evidence} short={result.strategy.short_evidence}"
                    )
                    print(
                        "RISK: "
                        f"status={result.proposal.risk.status.value} quantity_limit={result.proposal.risk.max_quantity} "
                        f"rr={result.proposal.risk.reward_risk} reasons={result.proposal.risk.reasons}"
                    )
                    if result.order is None:
                        print("DONE: trigger crossed but RiskEngine rejected; no eToro demo order")
                        return 5
                    response = demo_broker.last_response or {}
                    data = response.get("data") if isinstance(response.get("data"), dict) else response
                    print(
                        "ETORO DEMO ORDER: "
                        f"order_id={data.get('orderId', result.order.order_id)} "
                        f"token={data.get('token')} reference_id={data.get('referenceId')} "
                        f"instrument={result.order.instrument} direction={result.order.direction.value} "
                        f"amount_usd={args.demo_order_amount_usd:g} status={result.order.status}"
                    )
                    print("DONE: order accepted by eToro DEMO endpoint; no real-money order endpoint exists in this broker")
                    return 0
    except TimeoutError:
        print("TIMEOUT: scheduled news eToro demo did not finish within the configured window")
        return 3
    finally:
        await stream.aclose()

    return 3


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
