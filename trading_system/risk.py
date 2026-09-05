from __future__ import annotations

import math
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
    max_position_value_usd: float | None = None
    extended_hours_max_spread_pct: float | None = None
    extended_hours_max_volatility_pct: float | None = None
    extended_hours_position_size_multiplier: float | None = None


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


class RiskEngine:
    """Deterministic gate between strategy output and every broker action."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        cap = self.config.max_position_value_usd
        if cap is not None and (not math.isfinite(float(cap)) or cap <= 0):
            raise ValueError("max_position_value_usd must be finite and positive")

        extended_spread = self.config.extended_hours_max_spread_pct
        if extended_spread is not None:
            if not math.isfinite(float(extended_spread)) or extended_spread <= 0:
                raise ValueError("extended_hours_max_spread_pct must be finite and positive")
            if extended_spread > self.config.max_spread_pct:
                raise ValueError("extended_hours_max_spread_pct must not exceed max_spread_pct")

        extended_volatility = self.config.extended_hours_max_volatility_pct
        if extended_volatility is not None:
            if not math.isfinite(float(extended_volatility)) or extended_volatility <= 0:
                raise ValueError("extended_hours_max_volatility_pct must be finite and positive")
            if extended_volatility > self.config.max_volatility_pct:
                raise ValueError(
                    "extended_hours_max_volatility_pct must not exceed max_volatility_pct"
                )

        extended_size = self.config.extended_hours_position_size_multiplier
        if extended_size is not None and (
            not math.isfinite(float(extended_size)) or extended_size <= 0 or extended_size > 1
        ):
            raise ValueError(
                "extended_hours_position_size_multiplier must be finite and in (0, 1]"
            )

    def evaluate(
        self,
        candidate: TradeCandidate,
        portfolio: PortfolioState,
        *,
        requested_mode: TradingMode = TradingMode.PAPER,
        now: datetime | None = None,
        allow_fractional_sizing: bool = False,
        uses_extended_hours: bool = False,
    ) -> TradeProposal:
        decision = self._evaluate(
            candidate=candidate,
            portfolio=portfolio,
            requested_mode=requested_mode,
            now=now,
            allow_fractional_sizing=allow_fractional_sizing,
            uses_extended_hours=uses_extended_hours,
        )
        return TradeProposal(candidate=candidate, risk=decision, mode=requested_mode)

    def _evaluate(
        self,
        candidate: TradeCandidate,
        portfolio: PortfolioState,
        requested_mode: TradingMode,
        now: datetime | None,
        allow_fractional_sizing: bool = False,
        uses_extended_hours: bool = False,
    ) -> RiskDecision:
        reasons: list[str] = []
        current_time = now or datetime.now(UTC)

        extended_policy = (
            self.config.extended_hours_max_spread_pct,
            self.config.extended_hours_max_volatility_pct,
            self.config.extended_hours_position_size_multiplier,
        )
        extended_policy_ready = all(value is not None for value in extended_policy)
        if uses_extended_hours and not extended_policy_ready:
            reasons.append("extended_hours_risk_policy_missing")

        sizing_multiplier = (
            float(self.config.extended_hours_position_size_multiplier)
            if uses_extended_hours and extended_policy_ready
            else 1.0
        )
        max_risk_amount = (
            max(portfolio.equity, 0.0)
            * (self.config.max_risk_per_trade_pct / 100.0)
            * sizing_multiplier
        )
        equity_position_limit = max(portfolio.equity, 0.0) * (
            self.config.max_position_pct / 100.0
        )
        max_position_value = min(equity_position_limit, max(portfolio.cash, 0.0))
        if self.config.max_position_value_usd is not None:
            max_position_value = min(max_position_value, self.config.max_position_value_usd)
        max_position_value *= sizing_multiplier

        if candidate.direction is Direction.NO_TRADE:
            return RiskDecision(
                status=RiskStatus.REJECT,
                reasons=("strategy_returned_no_trade",),
                max_risk_amount=max_risk_amount,
                max_position_value=max_position_value,
                max_quantity=0,
                max_fractional_notional_usd=0.0,
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
        if portfolio.cash < 0:
            reasons.append("invalid_portfolio_cash")
        elif portfolio.cash == 0:
            reasons.append("insufficient_cash")
        if portfolio.open_positions >= self.config.max_open_positions:
            reasons.append("max_open_positions_reached")
        if portfolio.instrument_exposure_pct >= self.config.max_instrument_exposure_pct:
            reasons.append("max_instrument_exposure_reached")

        daily_loss_limit = max(portfolio.equity, 0.0) * (self.config.max_daily_loss_pct / 100.0)
        if portfolio.daily_pnl is None:
            reasons.append("missing_daily_pnl")
        elif not math.isfinite(portfolio.daily_pnl):
            reasons.append("invalid_daily_pnl")
        elif portfolio.daily_pnl <= -daily_loss_limit and daily_loss_limit > 0:
            reasons.append("max_daily_loss_reached")

        if portfolio.spread_pct is None:
            reasons.append("missing_spread_data")
        elif portfolio.spread_pct > self.config.max_spread_pct:
            reasons.append("spread_too_wide")
        elif (
            uses_extended_hours
            and extended_policy_ready
            and portfolio.spread_pct > float(self.config.extended_hours_max_spread_pct)
        ):
            reasons.append("extended_hours_spread_too_wide")

        if portfolio.volatility_pct is None:
            reasons.append("missing_volatility_data")
        elif portfolio.volatility_pct > self.config.max_volatility_pct:
            reasons.append("volatility_too_high")
        elif (
            uses_extended_hours
            and extended_policy_ready
            and portfolio.volatility_pct > float(self.config.extended_hours_max_volatility_pct)
        ):
            reasons.append("extended_hours_volatility_too_high")

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
        max_fractional_notional_usd = 0.0
        if entry and entry > 0 and risk_per_unit_decimal > 0:
            max_risk_decimal = _decimal(max_risk_amount)
            max_position_decimal = _decimal(max_position_value)
            entry_decimal = _decimal(entry)
            by_risk_units = max_risk_decimal / risk_per_unit_decimal
            by_position_units = max_position_decimal / entry_decimal
            approved_units = max(Decimal("0"), min(by_risk_units, by_position_units))
            max_fractional_notional_usd = float(approved_units * entry_decimal)
            by_risk = int(by_risk_units.to_integral_value(rounding=ROUND_FLOOR))
            by_position_value = int(by_position_units.to_integral_value(rounding=ROUND_FLOOR))
            max_quantity = max(0, min(by_risk, by_position_value))
            if max_quantity < 1 and not (
                allow_fractional_sizing and max_fractional_notional_usd > 0
            ):
                reasons.append("position_size_below_one_unit")

        if reasons:
            return RiskDecision(
                status=RiskStatus.REJECT,
                reasons=tuple(dict.fromkeys(reasons)),
                max_risk_amount=max_risk_amount,
                max_position_value=max_position_value,
                max_quantity=0,
                max_fractional_notional_usd=0.0,
                reward_risk=reward_risk,
            )
        return RiskDecision(
            status=RiskStatus.PASS,
            reasons=(),
            max_risk_amount=max_risk_amount,
            max_position_value=max_position_value,
            max_quantity=max_quantity,
            max_fractional_notional_usd=max_fractional_notional_usd,
            reward_risk=reward_risk,
        )
