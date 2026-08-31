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
    fractional_notional: float | None = None,
    proposal_id: str = "proposal-stable-id",
) -> TradeProposal:
    approved_notional = max_position_value if fractional_notional is None else fractional_notional
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
            max_quantity=0,
            max_fractional_notional_usd=approved_notional,
            reward_risk=2.0,
        ),
        mode=TradingMode.PAPER,
        proposal_id=proposal_id,
    )


def _portfolio(
    position_id: str = "p-123",
    *,
    amount: float = 500.0,
    direction: Direction = Direction.LONG,
    equity: float = 10_000.0,
    cash: float = 8_000.0,
) -> dict:
    stop = 75810.0 if direction is Direction.LONG else 76190.0
    target = 76380.0 if direction is Direction.LONG else 75620.0
    return {
        "data": {
            "equity": equity,
            "availableCash": cash,
            "positions": [
                {
                    "positionId": position_id,
                    "instrumentId": 100000,
                    "amount": amount,
                    "units": amount / 76000.0,
                    "stopLossRate": stop,
                    "takeProfitRate": target,
                }
            ],
        }
    }


class EtoroDemoBrokerTests(unittest.TestCase):
    def test_declares_fractional_sizing_capability(self) -> None:
        broker = EtoroDemoBroker(api_key="api", user_key="user", instrument_id=100000)
        self.assertIs(broker.supports_fractional_sizing, True)

    def test_preflight_uses_demo_portfolio_path(self) -> None:
        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            return _FakeResponse({"data": {"positions": []}})

        broker = EtoroDemoBroker(api_key="api", user_key="user", instrument_id=100000, http_get=fake_get)
        broker.verify_demo_access()
        self.assertEqual(calls[0][0], EtoroDemoBroker.DEMO_PORTFOLIO_URL)

    def test_live_portfolio_drives_risk_state(self) -> None:
        def fake_get(_url, **_kwargs):
            return _FakeResponse(_portfolio(amount=750.0, equity=12_000.0, cash=7_500.0))

        broker = EtoroDemoBroker(
            api_key="api", user_key="user", instrument_id=100000, http_get=fake_get
        )
        state = broker.risk_portfolio_state(spread_pct=0.25, daily_pnl=-50.0)
        self.assertEqual(state.equity, 12_000.0)
        self.assertEqual(state.cash, 7_500.0)
        self.assertEqual(state.open_positions, 1)
        self.assertAlmostEqual(state.instrument_exposure_pct, 6.25)
        self.assertEqual(state.daily_pnl, -50.0)

    def test_live_portfolio_missing_cash_fails_closed(self) -> None:
        def fake_get(_url, **_kwargs):
            return _FakeResponse({"data": {"equity": 10_000.0, "positions": []}})

        broker = EtoroDemoBroker(
            api_key="api", user_key="user", instrument_id=100000, http_get=fake_get
        )
        with self.assertRaisesRegex(RuntimeError, "authoritative account field"):
            broker.risk_portfolio_state(spread_pct=0.2)

    def test_execute_reconciles_open_position_and_protection_before_filled(self) -> None:
        post_calls = []
        get_calls = []

        def fake_post(url, **kwargs):
            post_calls.append((url, kwargs))
            return _FakeResponse({"data": {"orderId": 123, "positionId": "p-123"}})

        def fake_get(url, **kwargs):
            get_calls.append((url, kwargs))
            return _FakeResponse(_portfolio())

        broker = EtoroDemoBroker(
            api_key="api", user_key="user", instrument_id=100000, amount_usd=500,
            http_get=fake_get, http_post=fake_post, reconcile_delay_seconds=0,
        )
        proposal = _proposal()
        order = broker.execute(proposal)
        payload = post_calls[0][1]["json"]
        self.assertEqual(payload["amount"], 500.0)
        self.assertEqual(payload["stopLossRate"], proposal.candidate.stop)
        self.assertEqual(payload["takeProfitRate"], proposal.candidate.target_1)
        self.assertEqual(post_calls[0][1]["headers"]["x-request-id"], proposal.proposal_id)
        self.assertGreaterEqual(len(get_calls), 1)
        self.assertEqual(order.status, "ETORO_DEMO_FILLED")
        self.assertEqual(order.notional_usd, 500.0)
        self.assertEqual(order.broker_position_id, "p-123")

    def test_reconciled_position_without_protection_stays_uncertain(self) -> None:
        def fake_post(_url, **_kwargs):
            return _FakeResponse({"data": {"orderId": 123, "positionId": "p-123"}})

        def fake_get(_url, **_kwargs):
            body = _portfolio()
            position = body["data"]["positions"][0]
            position.pop("stopLossRate")
            position.pop("takeProfitRate")
            return _FakeResponse(body)

        broker = EtoroDemoBroker(
            api_key="api", user_key="user", instrument_id=100000,
            http_get=fake_get, http_post=fake_post, reconcile_delay_seconds=0,
        )
        with self.assertRaisesRegex(RuntimeError, "broker-side protection"):
            broker.execute(_proposal())

    def test_order_amount_uses_fractional_risk_notional(self) -> None:
        captured = {}

        def fake_post(_url, **kwargs):
            captured.update(kwargs["json"])
            return _FakeResponse({"data": {"orderId": 789, "positionId": "p-125"}})

        def fake_get(_url, **_kwargs):
            return _FakeResponse(_portfolio("p-125", amount=125.0))

        broker = EtoroDemoBroker(
            api_key="api", user_key="user", instrument_id=100000, amount_usd=500,
            http_get=fake_get, http_post=fake_post, reconcile_delay_seconds=0,
        )
        order = broker.execute(_proposal(max_position_value=1000.0, fractional_notional=125.0))
        self.assertEqual(captured["amount"], 125.0)
        self.assertEqual(order.notional_usd, 125.0)
        self.assertEqual(order.quantity, 0)

    def test_acceptance_without_reconciled_position_stays_uncertain(self) -> None:
        def fake_post(_url, **_kwargs):
            return _FakeResponse({"data": {"orderId": 456, "positionId": "pending-pos"}})

        def fake_get(_url, **_kwargs):
            return _FakeResponse({"data": {"positions": []}})

        broker = EtoroDemoBroker(
            api_key="api", user_key="user", instrument_id=100000,
            http_get=fake_get, http_post=fake_post, reconcile_attempts=2, reconcile_delay_seconds=0,
        )
        with self.assertRaisesRegex(RuntimeError, "could not be reconciled"):
            broker.execute(_proposal())

    def test_acceptance_without_position_id_is_not_completed(self) -> None:
        def fake_post(_url, **_kwargs):
            return _FakeResponse({"data": {"orderId": 456}})

        broker = EtoroDemoBroker(api_key="api", user_key="user", instrument_id=100000, http_post=fake_post)
        with self.assertRaisesRegex(RuntimeError, "without a position id"):
            broker.execute(_proposal())

    def test_short_demo_signal_uses_sell_transaction_and_short_protection(self) -> None:
        captured = {}

        def fake_post(_url, **kwargs):
            captured.update(kwargs["json"])
            return _FakeResponse({"data": {"orderId": 456, "positionId": "p-short"}})

        def fake_get(_url, **_kwargs):
            return _FakeResponse(_portfolio("p-short", amount=500.0, direction=Direction.SHORT))

        broker = EtoroDemoBroker(
            api_key="api", user_key="user", instrument_id=100000,
            http_get=fake_get, http_post=fake_post, reconcile_delay_seconds=0,
        )
        proposal = _proposal(Direction.SHORT)
        broker.execute(proposal)
        self.assertEqual(captured["transaction"], "sell")
        self.assertEqual(captured["stopLossRate"], proposal.candidate.stop)
        self.assertEqual(captured["takeProfitRate"], proposal.candidate.target_1)

    def test_zero_fractional_risk_notional_is_rejected_before_http_call(self) -> None:
        called = False

        def fake_post(*_args, **_kwargs):
            nonlocal called
            called = True
            return _FakeResponse({"orderId": 1})

        broker = EtoroDemoBroker(api_key="api", user_key="user", instrument_id=100000, http_post=fake_post)
        with self.assertRaisesRegex(ValueError, "fractional notional"):
            broker.execute(_proposal(fractional_notional=0.0))
        self.assertFalse(called)

    def test_live_mode_is_rejected_before_http_call(self) -> None:
        called = False

        def fake_post(*_args, **_kwargs):
            nonlocal called
            called = True
            return _FakeResponse({"orderId": 1})

        proposal = _proposal()
        live = TradeProposal(candidate=proposal.candidate, risk=proposal.risk, mode=TradingMode.LIVE, proposal_id=proposal.proposal_id)
        broker = EtoroDemoBroker(api_key="api", user_key="user", instrument_id=100000, http_post=fake_post)
        with self.assertRaisesRegex(ValueError, "only accepts PAPER"):
            broker.execute(live)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
