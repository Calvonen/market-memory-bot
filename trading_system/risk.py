from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_FLOOR

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


def _decimal(value: float) -> Decimal:
    """Convert configured/market floats through their decimal string form.

    Risk sizing must not lose a whole unit because binary floating-point represents
    simple decimal prices such as 0.80 - 0.76 as 0.040000000000000036.
    """
    return Decimal(str(value))


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
        current_time = now or datetime.now(UTC)

        max_risk_amount = max(portfolio.equity, 0.0) * (self.config.max_risk_per_trade_pct / 100.0)
        max_position_value = max(portfolio.equity, 0.0) * (self.config.max_position_pct / 100.0)

        # A StrategyEngine NO_TRADE is already a complete, deterministic rejection.
        # Do not run entry/stop/target geometry for a direction that will never be
        # sent to a broker; doing so only adds misleading secondary reasons.
        if candidate.direction is Direction.NO_TRADE:
            return RiskDecision(
                status=RiskStatus.REJECT,
                reasons=("strategy_returned_no_trade",),
                max_risk_amount=max_risk_amount,
                max_position_value=max_position_value,
                max_quantity=0,
                reward_risk=None,
            )

        if self.config.kill_switch:
            reasons.append("kill_switch_active")

        if requested_mode is TradingMode.LIVE and not self.config.live_trading_enabled:
            reasons.append("live_trading_disabled")

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

        # A deterministic gate must fail closed on missing market-quality data,
        # not silently skip the check: an absent spread/volatility reading is
        # not evidence that the trade is safe, and callers must never be able
        # to reach PASS by simply omitting these fields from PortfolioState.
        if portfolio.spread_pct is None:
            reasons.append("missing_spread_data")
        elif portfolio.spread_pct > self.config.max_spread_pct:
            reasons.append("spread_too_wide")

        if portfolio.volatility_pct is None:
            reasons.append("missing_volatility_data")
        elif portfolio.volatility_pct > self.config.max_volatility_pct:
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
        risk_per_unit_decimal = Decimal("0")

        if entry and stop and target and entry > 0 and stop > 0 and target > 0:
            entry_decimal = _decimal(entry)
            stop_decimal = _decimal(stop)
            target_decimal = _decimal(target)

            if candidate.direction is Direction.LONG:
                risk_per_unit_decimal = entry_decimal - stop_decimal
                reward_per_unit_decimal = target_decimal - entry_decimal
            else:
                risk_per_unit_decimal = stop_decimal - entry_decimal
                reward_per_unit_decimal = entry_decimal - target_decimal

            if risk_per_unit_decimal <= 0:
                reasons.append("stop_on_wrong_side")
            if reward_per_unit_decimal <= 0:
                reasons.append("target_on_wrong_side")

            if risk_per_unit_decimal > 0 and reward_per_unit_decimal > 0:
                reward_risk_decimal = reward_per_unit_decimal / risk_per_unit_decimal
                reward_risk = float(reward_risk_decimal)
                if reward_risk_decimal < _decimal(self.config.min_reward_risk):
                    reasons.append("reward_risk_below_minimum")

        max_quantity = 0

        if entry and entry > 0 and risk_per_unit_decimal > 0:
            max_risk_decimal = _decimal(max_risk_amount)
            max_position_decimal = _decimal(max_position_value)
            entry_decimal = _decimal(entry)
            by_risk = int((max_risk_decimal / risk_per_unit_decimal).to_integral_value(rounding=ROUND_FLOOR))
            by_position_value = int((max_position_decimal / entry_decimal).to_integral_value(rounding=ROUND_FLOOR))
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
