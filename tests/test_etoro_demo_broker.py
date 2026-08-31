from __future__ import annotations

import unittest

from trading_system.brokers.etoro_demo import EtoroDemoBroker
from trading_system.models import (
    Direction,
    RiskDecision,
    RiskStatus,
    TradeCandidate,
    TradeProposal,
    TradingMode,
)


class _FakeResponse:
    def __init__(self, body: dict, *, ok: bool = True, status_code: int = 200, text: str = "") -> None:
        self._body = body
        self.ok = ok
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._body


def _proposal(
    direction: Direction = Direction.LONG,
    *,
    max_position_value: float = 1000.0,
    proposal_id: str = "proposal-stable-id",
) -> TradeProposal:
    return TradeProposal(
        candidate=TradeCandidate(
            instrument="BTC",
            direction=direction,
            confidence=65,
            entry=76000.0,
            stop=75810.0 if direction is Direction.LONG else 76190.0,
            target_1=76380.0 if direction is Direction.LONG else 75620.0,
        ),
        risk=RiskDecision(
            status=RiskStatus.PASS,
            reasons=(),
            max_position_value=max_position_value,
            max_quantity=1,
            reward_risk=2.0,
        ),
        mode=TradingMode.PAPER,
        proposal_id=proposal_id,
    )


class EtoroDemoBrokerTests(unittest.TestCase):
    def test_preflight_uses_demo_portfolio_path(self) -> None:
        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            return _FakeResponse({"data": {}})

        broker = EtoroDemoBroker(
            api_key="api",
            user_key="user",
            instrument_id=100000,
            http_get=fake_get,
        )
        broker.verify_demo_access()
        self.assertEqual(calls[0][0], EtoroDemoBroker.DEMO_PORTFOLIO_URL)
        self.assertIn("/demo/portfolio", calls[0][0])

    def test_execute_posts_only_opening_order_to_demo_endpoint(self) -> None:
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return _FakeResponse({"orderId": 123, "token": "tok", "referenceId": "ref"})

        broker = EtoroDemoBroker(
            api_key="api",
            user_key="user",
            instrument_id=100000,
            amount_usd=500,
            http_post=fake_post,
        )
        proposal = _proposal()
        order = broker.execute(proposal)

        payload = calls[0][1]["json"]
        headers = calls[0][1]["headers"]
        self.assertEqual(calls[0][0], EtoroDemoBroker.DEMO_ORDERS_URL)
        self.assertIn("/execution/demo/orders", calls[0][0])
        self.assertEqual(payload["transaction"], "buy")
        self.assertEqual(payload["amount"], 500.0)
        self.assertEqual(headers["x-request-id"], proposal.proposal_id)
        self.assertNotIn("stopLossRate", payload)
        self.assertNotIn("stopLossType", payload)
        self.assertNotIn("takeProfitRate", payload)
        self.assertNotIn("takeProfitType", payload)
        self.assertEqual(order.status, "ETORO_DEMO_ACCEPTED")
        self.assertEqual(broker.last_request_id, proposal.proposal_id)
        self.assertEqual(broker.last_submitted_amount_usd, 500.0)

    def test_order_amount_is_capped_by_risk_approved_position_value(self) -> None:
        captured = {}

        def fake_post(_url, **kwargs):
            captured.update(kwargs["json"])
            return _FakeResponse({"orderId": 789})

        broker = EtoroDemoBroker(
            api_key="api",
            user_key="user",
            instrument_id=100000,
            amount_usd=500,
            http_post=fake_post,
        )
        broker.execute(_proposal(max_position_value=125.0))
        self.assertEqual(captured["amount"], 125.0)

    def test_short_demo_signal_uses_sell_transaction(self) -> None:
        captured = {}

        def fake_post(_url, **kwargs):
            captured.update(kwargs["json"])
            return _FakeResponse({"orderId": 456})

        broker = EtoroDemoBroker(
            api_key="api",
            user_key="user",
            instrument_id=100000,
            http_post=fake_post,
        )
        broker.execute(_proposal(Direction.SHORT))
        self.assertEqual(captured["transaction"], "sell")

    def test_zero_risk_position_value_is_rejected_before_http_call(self) -> None:
        called = False

        def fake_post(*_args, **_kwargs):
            nonlocal called
            called = True
            return _FakeResponse({"orderId": 1})

        broker = EtoroDemoBroker(
            api_key="api",
            user_key="user",
            instrument_id=100000,
            http_post=fake_post,
        )
        with self.assertRaisesRegex(ValueError, "executable position value"):
            broker.execute(_proposal(max_position_value=0.0))
        self.assertFalse(called)

    def test_live_mode_is_rejected_before_http_call(self) -> None:
        called = False

        def fake_post(*_args, **_kwargs):
            nonlocal called
            called = True
            return _FakeResponse({"orderId": 1})

        proposal = _proposal()
        live = TradeProposal(
            candidate=proposal.candidate,
            risk=proposal.risk,
            mode=TradingMode.LIVE,
            proposal_id=proposal.proposal_id,
        )
        broker = EtoroDemoBroker(
            api_key="api",
            user_key="user",
            instrument_id=100000,
            http_post=fake_post,
        )
        with self.assertRaisesRegex(ValueError, "only accepts PAPER"):
            broker.execute(live)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
