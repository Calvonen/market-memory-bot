from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from trading_system.models import (
    Direction,
    PortfolioState,
    RiskDecision,
    RiskStatus,
    TradeCandidate,
    TradeProposal,
    TradingMode,
)


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = 20.0
    max_risk_per_trade_pct: float = 0.5
    min_reward_risk: float = 1.5
    max_open_positions: int = 5
    max_instrument_exposure_pct: float = 25.0
    max_daily_loss_pct: float = 2.0
    max_spread_pct: float = 1.0
    max_volatility_pct: float = 12.0
    cooldown_after_loss_minutes: int = 60
    kill_switch: bool = False
    live_trading_enabled: bool = False


class RiskEngine:
    """Deterministic gate between strategy output and every broker action."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    def evaluate(
        self,
        candidate: TradeCandidate,
        portfolio: PortfolioState,
        *,
        requested_mode: TradingMode = TradingMode.PAPER,
        now: datetime | None = None,
    ) -> TradeProposal:
        decision = self._evaluate(candidate=candidate, portfolio=portfolio, requested_mode=requested_mode, now=now)
        return TradeProposal(candidate=candidate, risk=decision, mode=requested_mode)

    def _evaluate(
        self,
        candidate: TradeCandidate,
        portfolio: PortfolioState,
        requested_mode: TradingMode,
        now: datetime | None,
    ) -> RiskDecision:
        reasons: list[str] = []
        current_time = now or datetime.utcnow()

        if self.config.kill_switch:
            reasons.append("kill_switch_active")

        if requested_mode is TradingMode.LIVE and not self.config.live_trading_enabled:
            reasons.append("live_trading_disabled")

        if candidate.direction is Direction.NO_TRADE:
            reasons.append("strategy_returned_no_trade")

        if candidate.direction not in {Direction.LONG, Direction.SHORT}:
            reasons.append("unsupported_direction")

        if portfolio.equity <= 0:
            reasons.append("invalid_portfolio_equity")

        if portfolio.open_positions >= self.config.max_open_positions:
            reasons.append("max_open_positions_reached")

        if portfolio.instrument_exposure_pct >= self.config.max_instrument_exposure_pct:
            reasons.append("max_instrument_exposure_reached")

        daily_loss_limit = max(portfolio.equity, 0.0) * (self.config.max_daily_loss_pct / 100.0)
        if portfolio.daily_pnl <= -daily_loss_limit and daily_loss_limit > 0:
            reasons.append("max_daily_loss_reached")

        if portfolio.spread_pct is not None and portfolio.spread_pct > self.config.max_spread_pct:
            reasons.append("spread_too_wide")

        if portfolio.volatility_pct is not None and portfolio.volatility_pct > self.config.max_volatility_pct:
            reasons.append("volatility_too_high")

        if portfolio.last_loss_at is not None:
            cooldown_until = portfolio.last_loss_at + timedelta(minutes=self.config.cooldown_after_loss_minutes)
            if current_time < cooldown_until:
                reasons.append("loss_cooldown_active")

        entry = candidate.entry
        stop = candidate.stop
        target = candidate.target_1

        if entry is None or entry <= 0:
            reasons.append("invalid_entry")
        if stop is None or stop <= 0:
            reasons.append("invalid_stop")
        if target is None or target <= 0:
            reasons.append("invalid_target")

        reward_risk: float | None = None
        risk_per_unit = 0.0

        if entry and stop and target and entry > 0 and stop > 0 and target > 0:
            if candidate.direction is Direction.LONG:
                risk_per_unit = entry - stop
                reward_per_unit = target - entry
            else:
                risk_per_unit = stop - entry
                reward_per_unit = entry - target

            if risk_per_unit <= 0:
                reasons.append("stop_on_wrong_side")
            if reward_per_unit <= 0:
                reasons.append("target_on_wrong_side")

            if risk_per_unit > 0 and reward_per_unit > 0:
                reward_risk = reward_per_unit / risk_per_unit
                if reward_risk < self.config.min_reward_risk:
                    reasons.append("reward_risk_below_minimum")

        max_risk_amount = max(portfolio.equity, 0.0) * (self.config.max_risk_per_trade_pct / 100.0)
        max_position_value = max(portfolio.equity, 0.0) * (self.config.max_position_pct / 100.0)
        max_quantity = 0

        if entry and entry > 0 and risk_per_unit > 0:
            by_risk = int(max_risk_amount // risk_per_unit)
            by_position_value = int(max_position_value // entry)
            max_quantity = max(0, min(by_risk, by_position_value))
            if max_quantity < 1:
                reasons.append("position_size_below_one_unit")

        if reasons:
            return RiskDecision(
                status=RiskStatus.REJECT,
                reasons=tuple(dict.fromkeys(reasons)),
                max_risk_amount=max_risk_amount,
                max_position_value=max_position_value,
                max_quantity=0,
                reward_risk=reward_risk,
            )

        return RiskDecision(
            status=RiskStatus.PASS,
            reasons=(),
            max_risk_amount=max_risk_amount,
            max_position_value=max_position_value,
            max_quantity=max_quantity,
            reward_risk=reward_risk,
        )
