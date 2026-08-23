from __future__ import annotations

import os
import uuid
from typing import Callable

import requests

from trading_system.brokers.base import Broker, BrokerOrder
from trading_system.models import Direction, RiskStatus, TradeProposal, TradingMode


class EtoroDemoBroker(Broker):
    """Submit risk-approved demo orders to eToro's Virtual Portfolio only.

    The execution URL is intentionally fixed to the documented ``/demo/orders``
    endpoint. This broker has no code path for the real-money execution endpoint.
    """

    DEMO_ORDERS_URL = "https://public-api.etoro.com/api/v2/trading/execution/demo/orders"

    def __init__(
        self,
        *,
        api_key: str,
        user_key: str,
        instrument_id: int,
        amount_usd: float = 500.0,
        timeout_seconds: float = 15.0,
        http_post: Callable = requests.post,
    ) -> None:
        if not api_key.strip() or not user_key.strip():
            raise ValueError("eToro API and user keys are required")
        if instrument_id <= 0:
            raise ValueError("instrument_id must be positive")
        if amount_usd <= 0:
            raise ValueError("amount_usd must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.api_key = api_key.strip()
        self.user_key = user_key.strip()
        self.instrument_id = instrument_id
        self.amount_usd = float(amount_usd)
        self.timeout_seconds = float(timeout_seconds)
        self._http_post = http_post
        self.last_response: dict | None = None
        self.last_request_id: str | None = None

    @classmethod
    def from_env(cls, *, instrument_id: int, amount_usd: float = 500.0) -> "EtoroDemoBroker":
        return cls(
            api_key=os.environ.get("ETORO_API_KEY", ""),
            user_key=os.environ.get("ETORO_USER_KEY", ""),
            instrument_id=instrument_id,
            amount_usd=amount_usd,
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
        if proposal.risk.max_quantity < 1:
            raise ValueError("Risk-approved quantity must be at least one")

        request_id = str(uuid.uuid4())
        transaction = "buy" if proposal.candidate.direction is Direction.LONG else "sell"
        payload = {
            "action": "open",
            "transaction": transaction,
            "instrumentId": self.instrument_id,
            "orderType": "mkt",
            "amount": self.amount_usd,
            "orderCurrency": "usd",
            "leverage": 1,
            "stopLossType": "fixed",
        }
        headers = {
            "x-api-key": self.api_key,
            "x-user-key": self.user_key,
            "x-request-id": request_id,
            "Content-Type": "application/json",
        }
        try:
            response = self._http_post(
                self.DEMO_ORDERS_URL,
                headers=headers,
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
        return BrokerOrder(
            order_id=str(raw_order_id),
            instrument=proposal.candidate.instrument,
            direction=proposal.candidate.direction,
            quantity=proposal.risk.max_quantity,
            reference_price=proposal.candidate.entry,
            status="ETORO_DEMO_ACCEPTED",
        )
