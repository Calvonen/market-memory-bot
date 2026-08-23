from __future__ import annotations

import argparse
import asyncio
import math
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
from trading_system.risk import RiskConfig, RiskEngine
from trading_system.tracked_event_reaction_live import stream_tracked_event_reaction_runtime
from trading_system.tracked_event_reaction_runtime import TrackedEventReactionRuntime
from trading_system.tracked_instrument_etoro import resolve_tracked_instrument
from trading_system.tracked_instruments import TrackedInstrumentSource, create_tracked_instrument


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a scheduled NEWS event against real eToro market data and, after StrategyEngine + RiskEngine approval, "
            "submit an opening market order to eToro's DEMO / Virtual Portfolio only. This proof runner does not attach "
            "protective stop-loss or take-profit orders at eToro; the stop/targets below are RiskEngine geometry only."
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


def _require_finite(value: float, *, name: str, minimum: float, inclusive: bool = True) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if inclusive:
        if value < minimum:
            raise ValueError(f"{name} must be at least {minimum:g}")
    elif value <= minimum:
        raise ValueError(f"{name} must be greater than {minimum:g}")


def _spread_pct_from_quote(quote) -> float:
    if quote.bid is None or quote.ask is None:
        raise ValueError("eToro quote is missing bid/ask for spread validation")
    mid = (quote.bid + quote.ask) / Decimal("2")
    if mid <= 0:
        raise ValueError("eToro quote midpoint must be positive")
    spread_pct = float(((quote.ask - quote.bid) / mid) * Decimal("100"))
    if not math.isfinite(spread_pct) or spread_pct < 0:
        raise ValueError("eToro quote produced an invalid spread")
    return spread_pct


def _demo_strategy_inputs(*, instrument: str, event_id: str, direction: Direction) -> StrategyInputs:
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
    stop_distance = 0.0025
    target_distance = 0.0050
    if direction is Direction.LONG:
        return TradeLevels(entry=entry, stop=entry * (1.0 - stop_distance), target_1=entry * (1.0 + target_distance), target_2=entry * (1.0 + 0.0075))
    return TradeLevels(entry=entry, stop=entry * (1.0 + stop_distance), target_1=entry * (1.0 - target_distance), target_2=entry * (1.0 - 0.0075))


def _minimum_equity_for_one_unit(levels: TradeLevels, config: RiskConfig) -> float:
    if levels.entry is None or levels.stop is None or levels.entry <= 0 or levels.stop <= 0:
        raise ValueError("valid entry and stop are required for demo equity sizing")
    if config.max_position_pct <= 0 or config.max_risk_per_trade_pct <= 0:
        raise ValueError("risk configuration cannot size a demo position")
    position_equity = levels.entry / (config.max_position_pct / 100.0)
    risk_per_unit = abs(levels.entry - levels.stop)
    risk_equity = risk_per_unit / (config.max_risk_per_trade_pct / 100.0)
    return max(position_equity, risk_equity) * 1.000001


async def _run(args: argparse.Namespace) -> int:
    _require_finite(args.trigger_pct, name="--trigger-pct", minimum=0.0)
    _require_finite(args.demo_order_amount_usd, name="--demo-order-amount-usd", minimum=0.0, inclusive=False)
    _require_finite(args.paper_equity, name="--paper-equity", minimum=0.0, inclusive=False)
    _require_finite(args.demo_volatility_pct, name="--demo-volatility-pct", minimum=0.0)
    _require_finite(args.timeout_seconds, name="--timeout-seconds", minimum=0.0, inclusive=False)
    if args.max_observations < 1:
        raise ValueError("--max-observations must be at least 1")

    event_at = _parse_aware_datetime(args.event_at)
    provider = EtoroMarketDataProvider.from_env()
    tracked = create_tracked_instrument(instrument=args.instrument, company_name=args.company_name, market=args.market, source=TrackedInstrumentSource.MANUAL)
    resolved = resolve_tracked_instrument(tracked, EtoroInstrumentResolver(provider))
    if resolved is None:
        print(f"RESOLVE FAILED: {tracked.instrument}")
        return 2

    initial_quote = provider.fetch_quote(resolved.etoro_instrument_id)
    initial_spread_pct = _spread_pct_from_quote(initial_quote)
    demo_broker = EtoroDemoBroker.from_env(instrument_id=resolved.etoro_instrument_id, amount_usd=args.demo_order_amount_usd)
    demo_broker.verify_demo_access()

    print(f"RESOLVED: tracked={resolved.instrument} market={resolved.market!r} etoro_id={resolved.etoro_instrument_id} symbol={resolved.etoro_symbol!r} name={resolved.etoro_display_name!r}")
    print("ETORO DEMO ACCESS: ok (Virtual Portfolio readable)")
    print(f"DEMO CONFIG: event_at={event_at.isoformat()} trigger={args.trigger_pct}% demo_order_amount_usd={args.demo_order_amount_usd:g} paper_equity_floor={args.paper_equity:g} initial_spread={initial_spread_pct:.6f}% demo_volatility={args.demo_volatility_pct}% ETORO_DEMO_ONLY=true PROTECTIVE_ORDERS=false")

    runtime = TrackedEventReactionRuntime()
    monitor = RegisteredMarketEventMonitor(runtime)
    stream = stream_tracked_event_reaction_runtime((resolved,), provider, runtime, reconnect=True)
    risk_config = RiskConfig()
    pipeline = PaperTradingPipeline(risk_engine=RiskEngine(risk_config), broker=demo_broker)
    event = None
    observation_count = 0

    try:
        async with asyncio.timeout(args.timeout_seconds):
            async for batch in stream:
                for candle in batch.candles:
                    print(f"CANDLE: {candle.interval_minutes}m start={candle.start.isoformat()} o={candle.open} h={candle.high} l={candle.low} c={candle.close}")

                if event is None:
                    pre_event_one_minute = [c for c in batch.candles if c.interval_minutes == 1 and c.start + timedelta(minutes=1) <= event_at]
                    if not pre_event_one_minute:
                        continue
                    event = register_news_market_event(monitor, resolved, event_id=f"scheduled-news-demo-{uuid4().hex[:12]}", event_at=event_at, title=args.title)
                    print(f"EVENT REGISTERED: id={event.event_id} source=news event_at={event.event_at.isoformat()} waiting for clean post-event candle")
                    continue

                observed_at = batch.update.update.timestamp or datetime.now(UTC)
                for registered in monitor.observe_batch(batch, observed_at=observed_at):
                    reaction = registered.observation.reaction.tracked_reaction.reaction
                    observation_count += 1
                    print(f"REACTION: #{observation_count} start={reaction.candle_start.isoformat()} reference={reaction.reference_price} close={reaction.close_price} return={reaction.return_pct}%")
                    move = float(reaction.return_pct)
                    if abs(move) < args.trigger_pct:
                        if observation_count >= args.max_observations:
                            print("DONE: demo trigger was not crossed; no eToro demo order")
                            return 4
                        continue

                    direction = Direction.LONG if move > 0 else Direction.SHORT
                    entry = float(reaction.close_price)
                    levels = _trade_levels(entry, direction)
                    decision_quote = provider.fetch_quote(resolved.etoro_instrument_id)
                    decision_spread_pct = _spread_pct_from_quote(decision_quote)
                    effective_equity = max(args.paper_equity, _minimum_equity_for_one_unit(levels, risk_config))
                    if effective_equity > args.paper_equity:
                        print(f"DEMO RISK SIZING: paper_equity adjusted from {args.paper_equity:g} to {effective_equity:.2f} to represent at least one whole unit; eToro demo order amount is unchanged")

                    result = pipeline.run(
                        _demo_strategy_inputs(instrument=resolved.instrument, event_id=event.event_id, direction=direction),
                        levels,
                        PortfolioState(equity=effective_equity, cash=effective_equity, open_positions=0, instrument_exposure_pct=0.0, daily_pnl=0.0, spread_pct=decision_spread_pct, volatility_pct=args.demo_volatility_pct),
                    )
                    print(f"STRATEGY: direction={result.strategy.direction.value} confidence={result.strategy.confidence} long={result.strategy.long_evidence} short={result.strategy.short_evidence}")
                    print(f"RISK: status={result.proposal.risk.status.value} quantity_limit={result.proposal.risk.max_quantity} rr={result.proposal.risk.reward_risk} spread={decision_spread_pct:.6f}% reasons={result.proposal.risk.reasons}")
                    if result.order is None:
                        print("DONE: trigger crossed but RiskEngine rejected; no eToro demo order")
                        return 5
                    response = demo_broker.last_response or {}
                    data = response.get("data") if isinstance(response.get("data"), dict) else response
                    print(f"ETORO DEMO ORDER: order_id={data.get('orderId', result.order.order_id)} token={data.get('token')} reference_id={data.get('referenceId')} instrument={result.order.instrument} direction={result.order.direction.value} amount_usd={args.demo_order_amount_usd:g} status={result.order.status}")
                    print("DONE: opening order accepted by eToro DEMO endpoint; no protective SL/TP was attached and no real-money order endpoint exists in this broker")
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
