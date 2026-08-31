from __future__ import annotations

import math
import os
import time
import uuid
from typing import Any, Callable

import requests

from trading_system.brokers.base import Broker, BrokerOrder
from trading_system.models import Direction, PortfolioState, RiskStatus, TradeProposal, TradingMode


class EtoroDemoBroker(Broker):
    """Submit and reconcile risk-approved eToro Virtual Portfolio orders only."""

    supports_fractional_sizing = True

    DEMO_PORTFOLIO_URL = "https://public-api.etoro.com/api/v1/trading/info/demo/portfolio"
    DEMO_ORDERS_URL = "https://public-api.etoro.com/api/v2/trading/execution/demo/orders"

    def __init__(
        self,
        *,
        api_key: str,
        user_key: str,
        instrument_id: int,
        amount_usd: float = 500.0,
        timeout_seconds: float = 15.0,
        reconcile_attempts: int = 5,
        reconcile_delay_seconds: float = 0.5,
        http_get: Callable = requests.get,
        http_post: Callable = requests.post,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.strip() or not user_key.strip():
            raise ValueError("eToro API and user keys are required")
        if instrument_id <= 0:
            raise ValueError("instrument_id must be positive")
        if not math.isfinite(float(amount_usd)) or amount_usd <= 0:
            raise ValueError("amount_usd must be a finite positive cap")
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if reconcile_attempts < 1:
            raise ValueError("reconcile_attempts must be positive")
        if not math.isfinite(float(reconcile_delay_seconds)) or reconcile_delay_seconds < 0:
            raise ValueError("reconcile_delay_seconds must be finite and non-negative")
        self.api_key = api_key.strip()
        self.user_key = user_key.strip()
        self.instrument_id = instrument_id
        self.amount_usd = float(amount_usd)
        self.timeout_seconds = float(timeout_seconds)
        self.reconcile_attempts = int(reconcile_attempts)
        self.reconcile_delay_seconds = float(reconcile_delay_seconds)
        self._http_get = http_get
        self._http_post = http_post
        self._sleep = sleep
        self.last_response: dict | None = None
        self.last_request_id: str | None = None
        self.last_submitted_amount_usd: float | None = None
        self.last_reconciled_notional_usd: float | None = None
        self.last_reconciled_position_id: str | None = None

    @classmethod
    def from_env(cls, *, instrument_id: int, amount_usd: float = 500.0) -> "EtoroDemoBroker":
        return cls(
            api_key=os.environ.get("ETORO_API_KEY", ""),
            user_key=os.environ.get("ETORO_USER_KEY", ""),
            instrument_id=instrument_id,
            amount_usd=amount_usd,
        )

    def _headers(self, request_id: str) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "x-user-key": self.user_key,
            "x-request-id": request_id,
            "Content-Type": "application/json",
        }

    def _get_demo_portfolio(self) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        try:
            response = self._http_get(
                self.DEMO_PORTFOLIO_URL,
                headers=self._headers(request_id),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"eToro demo portfolio check failed: {exc}") from exc
        if not response.ok:
            raise RuntimeError(f"eToro demo portfolio HTTP {response.status_code}: {response.text[:500]}")
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("eToro demo portfolio returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise RuntimeError("eToro demo portfolio returned an invalid response")
        return body

    def verify_demo_access(self) -> None:
        self._get_demo_portfolio()

    def preflight_execution(self, _proposal: TradeProposal) -> None:
        self.verify_demo_access()

    def _approved_amount_usd(self, proposal: TradeProposal) -> float:
        risk_notional = float(proposal.risk.max_fractional_notional_usd)
        if not math.isfinite(risk_notional) or risk_notional <= 0:
            raise ValueError("RiskEngine proposal has no finite positive fractional notional")
        amount = min(self.amount_usd, risk_notional)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("Risk-approved eToro demo amount must be positive")
        return float(amount)

    @staticmethod
    def _portfolio_containers(body: dict[str, Any]) -> list[dict[str, Any]]:
        containers: list[dict[str, Any]] = [body]
        data = body.get("data")
        if isinstance(data, dict):
            containers.append(data)
            nested = data.get("clientPortfolio")
            if isinstance(nested, dict):
                containers.append(nested)
        direct = body.get("clientPortfolio")
        if isinstance(direct, dict):
            containers.append(direct)
        return containers

    @classmethod
    def _portfolio_positions(cls, body: dict[str, Any]) -> list[dict[str, Any]]:
        for container in cls._portfolio_containers(body):
            positions = container.get("positions")
            if isinstance(positions, list):
                if not all(isinstance(row, dict) for row in positions):
                    raise RuntimeError("eToro demo portfolio contains malformed positions")
                return positions
        raise RuntimeError("eToro demo portfolio is missing positions")

    @staticmethod
    def _field(row: dict[str, Any], *names: str) -> Any:
        for name in names:
            value = row.get(name)
            if value not in (None, ""):
                return value
        return None

    @classmethod
    def _portfolio_scalar(cls, body: dict[str, Any], *names: str) -> float:
        for container in cls._portfolio_containers(body):
            raw = cls._field(container, *names)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("eToro demo portfolio contains malformed account balance") from exc
            if not math.isfinite(value):
                raise RuntimeError("eToro demo portfolio contains non-finite account balance")
            return value
        raise RuntimeError(f"eToro demo portfolio is missing authoritative account field: {names[0]}")

    @classmethod
    def _position_notional(cls, position: dict[str, Any]) -> float:
        raw_amount = cls._field(
            position, "amount", "Amount", "investedAmount", "InvestedAmount", "openAmount"
        )
        if raw_amount is not None:
            try:
                notional = abs(float(raw_amount))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("eToro demo position has invalid amount") from exc
        else:
            raw_units = cls._field(position, "units", "Units", "amountInUnits")
            raw_rate = cls._field(position, "openRate", "openPrice", "averageOpenPrice", "avgOpenPrice")
            try:
                notional = abs(float(raw_units) * float(raw_rate))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("eToro demo position is missing authoritative amount/units") from exc
        if not math.isfinite(notional) or notional <= 0:
            raise RuntimeError("eToro demo position has invalid notional")
        return notional

    def risk_portfolio_state(self, *, spread_pct: float, daily_pnl: float | None = None) -> PortfolioState:
        """Build RiskEngine state only from the current Virtual Portfolio snapshot."""
        if not math.isfinite(spread_pct) or spread_pct < 0:
            raise ValueError("spread_pct must be finite and non-negative")
        # Kept only for call-site compatibility. A caller-supplied value must never
        # authorize execution because it can be stale relative to the demo account.
        _ = daily_pnl
        body = self._get_demo_portfolio()
        equity = self._portfolio_scalar(body, "equity", "Equity", "totalEquity", "netLiquidationValue", "balance")
        cash = self._portfolio_scalar(
            body,
            "cash",
            "Cash",
            "availableCash",
            "availableBalance",
            "AvailableBalance",
            "cashAvailable",
            "freeCash",
        )
        current_daily_pnl = self._portfolio_scalar(
            body,
            "dailyPnl",
            "dailyPnL",
            "DailyPnl",
            "DailyPnL",
            "dailyProfitLoss",
            "DailyProfitLoss",
            "profitLossToday",
            "pnlToday",
        )
        if equity <= 0 or cash < 0:
            raise RuntimeError("eToro demo portfolio returned invalid live equity/cash")
        positions = self._portfolio_positions(body)
        instrument_notional = 0.0
        for position in positions:
            raw_instrument_id = self._field(position, "instrumentId", "instrumentID", "InstrumentID")
            try:
                position_instrument_id = int(raw_instrument_id)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("eToro demo position has invalid instrument id") from exc
            notional = self._position_notional(position)
            if position_instrument_id == self.instrument_id:
                instrument_notional += notional
        return PortfolioState(
            equity=equity,
            cash=cash,
            open_positions=len(positions),
            instrument_exposure_pct=(instrument_notional / equity) * 100.0,
            daily_pnl=current_daily_pnl,
            spread_pct=spread_pct,
            volatility_pct=None,
        )

    @staticmethod
    def _protected_rate(position: dict[str, Any], *names: str) -> float:
        raw = EtoroDemoBroker._field(position, *names)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("reconciled eToro position is missing broker-side protection") from exc
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError("reconciled eToro position has invalid broker-side protection")
        return value

    @staticmethod
    def _same_rate(observed: float, expected: float) -> bool:
        # Do not use a price-relative tolerance here: on a high-priced instrument
        # it could accept protection tens of dollars away from the authorized rate.
        # One cent is intentionally fail-closed until instrument tick precision is
        # available as authoritative metadata.
        return math.isclose(observed, expected, rel_tol=0.0, abs_tol=0.01)

    def _reconciled_position(
        self,
        *,
        position_id: str,
        submitted_amount_usd: float,
        expected_stop: float,
        expected_target: float,
    ) -> tuple[float, float | None]:
        for attempt in range(self.reconcile_attempts):
            positions = self._portfolio_positions(self._get_demo_portfolio())
            for position in positions:
                raw_position_id = self._field(position, "positionId", "positionID", "PositionID", "id")
                if str(raw_position_id or "") != position_id:
                    continue
                raw_instrument_id = self._field(position, "instrumentId", "instrumentID", "InstrumentID")
                try:
                    observed_instrument_id = int(raw_instrument_id)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("reconciled eToro position has invalid instrument id") from exc
                if observed_instrument_id != self.instrument_id:
                    raise RuntimeError("reconciled eToro position belongs to a different instrument")

                notional = self._position_notional(position)
                if notional > submitted_amount_usd * 1.001 + 0.01:
                    raise RuntimeError("reconciled eToro position exceeds submitted risk-bounded amount")

                stop_rate = self._protected_rate(position, "stopLossRate", "StopLossRate", "stopLoss")
                target_rate = self._protected_rate(position, "takeProfitRate", "TakeProfitRate", "takeProfit")
                if not self._same_rate(stop_rate, expected_stop):
                    raise RuntimeError("reconciled eToro position stop-loss differs from RiskEngine stop")
                if not self._same_rate(target_rate, expected_target):
                    raise RuntimeError("reconciled eToro position take-profit differs from RiskEngine target")

                raw_units = self._field(position, "units", "Units", "amountInUnits")
                units_value: float | None = None
                if raw_units is not None:
                    try:
                        candidate_units = abs(float(raw_units))
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError("reconciled eToro position has invalid units") from exc
                    if math.isfinite(candidate_units) and candidate_units > 0:
                        units_value = candidate_units
                return notional, units_value
            if attempt + 1 < self.reconcile_attempts and self.reconcile_delay_seconds:
                self._sleep(self.reconcile_delay_seconds)
        raise RuntimeError(
            "eToro demo order acceptance could not be reconciled to an open Virtual Portfolio position"
        )

    def execute(self, proposal: TradeProposal) -> BrokerOrder:
        if proposal.mode is not TradingMode.PAPER:
            raise ValueError("EtoroDemoBroker only accepts PAPER-mode proposals")
        if proposal.risk.status is not RiskStatus.PASS:
            raise ValueError("RiskEngine did not approve this proposal")
        if proposal.candidate.direction not in {Direction.LONG, Direction.SHORT}:
            raise ValueError("EtoroDemoBroker cannot execute NO_TRADE")
        if proposal.candidate.entry is None or proposal.candidate.entry <= 0:
            raise ValueError("Proposal has no valid entry price")
        if proposal.candidate.stop is None or not math.isfinite(float(proposal.candidate.stop)) or proposal.candidate.stop <= 0:
            raise ValueError("Proposal has no valid protective stop")
        if proposal.candidate.target_1 is None or not math.isfinite(float(proposal.candidate.target_1)) or proposal.candidate.target_1 <= 0:
            raise ValueError("Proposal has no valid protective target")
        if not proposal.proposal_id.strip():
            raise ValueError("Trade proposal must have a stable proposal id")

        request_id = proposal.proposal_id
        transaction = "buy" if proposal.candidate.direction is Direction.LONG else "sell"
        amount_usd = self._approved_amount_usd(proposal)
        stop_rate = float(proposal.candidate.stop)
        target_rate = float(proposal.candidate.target_1)
        payload = {
            "action": "open",
            "transaction": transaction,
            "instrumentId": self.instrument_id,
            "orderType": "mkt",
            "amount": amount_usd,
            "orderCurrency": "usd",
            "leverage": 1,
            "stopLossRate": stop_rate,
            "takeProfitRate": target_rate,
        }
        try:
            response = self._http_post(
                self.DEMO_ORDERS_URL,
                headers=self._headers(request_id),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"eToro demo order request failed: {exc}") from exc

        if not response.ok:
            raise RuntimeError(f"eToro demo order HTTP {response.status_code}: {response.text[:500]}")
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("eToro demo order returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise RuntimeError("eToro demo order returned an invalid response")

        data = body.get("data") if isinstance(body.get("data"), dict) else body
        raw_order_id = data.get("orderId") or data.get("token") or data.get("referenceId")
        raw_position_id = data.get("positionId") or data.get("positionID")
        if raw_order_id in (None, "") and raw_position_id in (None, ""):
            raise RuntimeError("eToro demo order response is missing order and position identifiers")
        if raw_position_id in (None, ""):
            raise RuntimeError(
                "eToro demo order was accepted without a position id; outcome requires reconciliation"
            )

        position_id = str(raw_position_id)
        reconciled_notional, _reconciled_units = self._reconciled_position(
            position_id=position_id,
            submitted_amount_usd=amount_usd,
            expected_stop=stop_rate,
            expected_target=target_rate,
        )

        self.last_response = body
        self.last_request_id = request_id
        self.last_submitted_amount_usd = amount_usd
        self.last_reconciled_notional_usd = reconciled_notional
        self.last_reconciled_position_id = position_id
        return BrokerOrder(
            order_id=str(raw_order_id or position_id),
            instrument=proposal.candidate.instrument,
            direction=proposal.candidate.direction,
            quantity=proposal.risk.max_quantity,
            reference_price=float(proposal.candidate.entry),
            status="ETORO_DEMO_FILLED",
            notional_usd=reconciled_notional,
            broker_position_id=position_id,
        )