from __future__ import annotations

import math
import os
import time
import uuid
from typing import Any, Callable

import requests

from trading_system.brokers.base import Broker, BrokerOrder
from trading_system.models import Direction, RiskStatus, TradeProposal, TradingMode


class EtoroDemoBroker(Broker):
    """Submit and reconcile risk-approved eToro Virtual Portfolio orders only."""

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
    def _portfolio_positions(body: dict[str, Any]) -> list[dict[str, Any]]:
        containers: list[Any] = [body]
        if isinstance(body.get("data"), dict):
            containers.append(body["data"])
        if isinstance(body.get("clientPortfolio"), dict):
            containers.append(body["clientPortfolio"])
        data = body.get("data")
        if isinstance(data, dict) and isinstance(data.get("clientPortfolio"), dict):
            containers.append(data["clientPortfolio"])
        for container in containers:
            positions = container.get("positions") if isinstance(container, dict) else None
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

    def _reconciled_position(
        self,
        *,
        position_id: str,
        submitted_amount_usd: float,
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

                raw_amount = self._field(
                    position, "amount", "Amount", "investedAmount", "InvestedAmount", "openAmount"
                )
                if raw_amount is not None:
                    try:
                        notional = float(raw_amount)
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError("reconciled eToro position has invalid amount") from exc
                    if not math.isfinite(notional) or notional <= 0:
                        raise RuntimeError("reconciled eToro position has non-positive amount")
                else:
                    raw_units = self._field(position, "units", "Units", "amountInUnits")
                    raw_rate = self._field(
                        position, "openRate", "openPrice", "averageOpenPrice", "avgOpenPrice"
                    )
                    try:
                        units = float(raw_units)
                        rate = float(raw_rate)
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError(
                            "reconciled eToro position is missing authoritative amount/units"
                        ) from exc
                    notional = abs(units * rate)
                    if not math.isfinite(notional) or notional <= 0:
                        raise RuntimeError("reconciled eToro position has invalid notional")

                if notional > submitted_amount_usd * 1.001 + 0.01:
                    raise RuntimeError("reconciled eToro position exceeds submitted risk-bounded amount")

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
        if not proposal.proposal_id.strip():
            raise ValueError("Trade proposal must have a stable proposal id")

        request_id = proposal.proposal_id
        transaction = "buy" if proposal.candidate.direction is Direction.LONG else "sell"
        amount_usd = self._approved_amount_usd(proposal)
        payload = {
            "action": "open",
            "transaction": transaction,
            "instrumentId": self.instrument_id,
            "orderType": "mkt",
            "amount": amount_usd,
            "orderCurrency": "usd",
            "leverage": 1,
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
