from __future__ import annotations

import math
import os
import uuid
from typing import Callable

import requests

from trading_system.brokers.base import Broker, BrokerOrder
from trading_system.models import Direction, RiskStatus, TradeProposal, TradingMode


class EtoroDemoBroker(Broker):
    """Submit risk-approved opening orders to eToro's Virtual Portfolio only.

    Both portfolio verification and execution are pinned to eToro's demo paths.
    This broker intentionally has no real-money execution path.

    The configured ``amount_usd`` is a hard demo-order cap, never an instruction
    to exceed RiskEngine sizing. The submitted amount is bounded by both the
    risk-approved position value and the approved quantity at the entry price.

    This proof broker currently submits only an opening market order. TradeProposal
    stop/target levels are RiskEngine inputs; they are not attached to the eToro
    position as protective stop-loss/take-profit orders here. Protective-order and
    position-lifecycle management belong in a separate follow-up implementation.
    """

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
        http_get: Callable = requests.get,
        http_post: Callable = requests.post,
    ) -> None:
        if not api_key.strip() or not user_key.strip():
            raise ValueError("eToro API and user keys are required")
        if instrument_id <= 0:
            raise ValueError("instrument_id must be positive")
        if not math.isfinite(float(amount_usd)) or amount_usd <= 0:
            raise ValueError("amount_usd must be a finite positive cap")
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.api_key = api_key.strip()
        self.user_key = user_key.strip()
        self.instrument_id = instrument_id
        self.amount_usd = float(amount_usd)
        self.timeout_seconds = float(timeout_seconds)
        self._http_get = http_get
        self._http_post = http_post
        self.last_response: dict | None = None
        self.last_request_id: str | None = None
        self.last_submitted_amount_usd: float | None = None

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

    def verify_demo_access(self) -> None:
        """Fail before execution authority is reserved if demo access is unavailable."""
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

    def _approved_amount_usd(self, proposal: TradeProposal) -> float:
        entry = float(proposal.candidate.entry or 0.0)
        position_cap = float(proposal.risk.max_position_value)
        quantity_cap = float(proposal.risk.max_quantity) * entry
        values = (entry, position_cap, quantity_cap)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("RiskEngine proposal has no finite positive executable position value")
        amount = min(self.amount_usd, position_cap, quantity_cap)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("Risk-approved eToro demo amount must be positive")
        return float(amount)

    def execute(self, proposal: TradeProposal) -> BrokerOrder:
        if proposal.mode is not TradingMode.PAPER:
            raise ValueError("EtoroDemoBroker only accepts PAPER-mode proposals")
        if proposal.risk.status is not RiskStatus.PASS:
            raise ValueError("RiskEngine did not approve this proposal")
        if proposal.candidate.direction not in {Direction.LONG, Direction.SHORT}:
            raise ValueError("EtoroDemoBroker cannot execute NO_TRADE")
        if proposal.candidate.entry is None or proposal.candidate.entry <= 0:
            raise ValueError("Proposal has no valid entry price")
        if proposal.risk.max_quantity < 1:
            raise ValueError("Risk-approved quantity must be at least one")
        if not proposal.proposal_id.strip():
            raise ValueError("Trade proposal must have a stable proposal id")

        # proposal_id is persisted in the durable broker-attempt risk audit before
        # this POST. Reusing it as x-request-id gives an exact correlation key for
        # later eToro reconciliation without claiming that x-request-id itself is
        # an exchange-side idempotency guarantee. A transport-uncertain POST still
        # remains fail-closed in the durable `started` attempt and is never retried.
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
        if raw_order_id in (None, ""):
            raise RuntimeError("eToro demo order response is missing an order identifier")

        self.last_response = body
        self.last_request_id = request_id
        self.last_submitted_amount_usd = amount_usd
        return BrokerOrder(
            order_id=str(raw_order_id),
            instrument=proposal.candidate.instrument,
            direction=proposal.candidate.direction,
            quantity=proposal.risk.max_quantity,
            reference_price=proposal.candidate.entry,
            status="ETORO_DEMO_ACCEPTED",
        )
