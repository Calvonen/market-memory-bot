from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.market_reaction import MarketReactionEngine, MarketReactionSnapshot
from trading_system.market_reaction_evolution import (
    MarketReactionEvolutionSnapshot,
    MarketReactionEvolutionTracker,
)
from trading_system.tracked_candle_pipeline import TrackedMarketCandle


@dataclass(frozen=True)
class TrackedMarketReaction:
    """One closed-candle reaction together with its deterministic evolution state."""

    reaction: MarketReactionSnapshot
    evolution: MarketReactionEvolutionSnapshot


class TrackedMarketReactionPipeline:
    """Compose reaction measurement and reaction evolution for tracked candles.

    The caller supplies an explicit reference price for each candle. The
    underlying evolution tracker locks that reference price for each
    ``(tracked_instrument_id, interval_minutes)`` sequence and fails closed if
    a later call tries to change the baseline or tracked identity.

    This pipeline adds no event discovery, persistence, strategy, risk, broker,
    or trade-veto behavior. It only connects the reviewed deterministic layers.
    """

    def __init__(
        self,
        *,
        engine: MarketReactionEngine | None = None,
        evolution_tracker: MarketReactionEvolutionTracker | None = None,
    ) -> None:
        self.engine = engine or MarketReactionEngine()
        self.evolution_tracker = evolution_tracker or MarketReactionEvolutionTracker()

    def add(
        self,
        candle: TrackedMarketCandle,
        *,
        reference_price: Decimal,
    ) -> TrackedMarketReaction:
        """Analyze one closed candle and classify how its reaction evolved."""
        reaction = self.engine.analyze(candle, reference_price=reference_price)
        evolution = self.evolution_tracker.add(reaction)
        return TrackedMarketReaction(reaction=reaction, evolution=evolution)
