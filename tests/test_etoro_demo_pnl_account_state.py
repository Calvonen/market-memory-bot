from __future__ import annotations

from dataclasses import replace
import unittest

from trading_system.brokers.etoro_demo import EtoroDemoBroker
from trading_system.models import Direction, RiskStatus, TradeCandidate
from trading_system.risk import RiskEngine


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = body
        self.ok = True
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._body


def _portfolio() -> dict:
    return {
        "data": {
            "positions": [
                {
                    "positionId": "p-1",
                    "instrumentId": 100000,
                    "amount": 750.0,
                    "units": 0.01,
                    "stopLossRate": 75000.0,
                    "takeProfitRate": 78000.0,
                }
            ]
        }
    }


def _documented_pnl_shape() -> dict:
    return {
        "clientPortfolio": {
            "credit": 1000.0,
            "unrealizedPnL": 175.0,
            "positions": [
                {"amount": 500.0, "unrealizedPnL": {"pnL": 50.0}},
                {"amount": 300.0, "unrealizedPnL": {"pnL": -20.0}},
            ],
            "mirrors": [
                {
                    "availableAmount": 100.0,
                    "closedPositionsNetProfit": 50.0,
                    "positions": [
                        {"amount": 200.0, "unrealizedPnL": {"pnL": 30.0}},
                        {"amount": 150.0, "unrealizedPnL": {"pnL": 15.0}},
                    ],
                }
            ],
            "ordersForOpen": [
                {
                    "mirrorID": 0,
                    "amount": 200.0,
                    "totalExternalCosts": 10.0,
                },
                {
                    "mirrorID": 99,
                    "amount": 100.0,
                    "totalExternalCosts": 5.0,
                },
            ],
            "orders": [{"amount": 150.0}],
        }
    }


def _broker_for_pnl(pnl: dict) -> EtoroDemoBroker:
    def fake_get(url, **_kwargs):
        if url == EtoroDemoBroker.DEMO_PORTFOLIO_URL:
            return _FakeResponse(_portfolio())
        if url == EtoroDemoBroker.DEMO_PNL_URL:
            return _FakeResponse(pnl)
        raise AssertionError(f"unexpected URL: {url}")

    return EtoroDemoBroker(
        api_key="api",
        user_key="user",
        instrument_id=100000,
        http_get=fake_get,
    )


class EtoroDemoPnlAccountStateTests(unittest.TestCase):
    def test_risk_state_uses_demo_pnl_for_equity_and_cash_without_relabeling_unrealized_pnl(self) -> None:
        calls: list[str] = []

        def fake_get(url, **_kwargs):
            calls.append(url)
            if url == EtoroDemoBroker.DEMO_PORTFOLIO_URL:
                return _FakeResponse(_portfolio())
            if url == EtoroDemoBroker.DEMO_PNL_URL:
                return _FakeResponse(_documented_pnl_shape())
            raise AssertionError(f"unexpected URL: {url}")

        broker = EtoroDemoBroker(
            api_key="api",
            user_key="user",
            instrument_id=100000,
            http_get=fake_get,
        )
        state = broker.risk_portfolio_state(spread_pct=0.2, daily_pnl=9999.0)

        # eToro's documented formulas:
        # available cash = 1000 - 200 manual open order - 150 pending order = 650
        # total invested = 800 + 350 + (100 - 50) + 200 + 150 + 10 = 1560
        # equity = 650 + 1560 + 175 = 2385
        self.assertEqual(state.cash, 650.0)
        self.assertEqual(state.equity, 2385.0)
        self.assertIsNone(state.daily_pnl)
        self.assertEqual(state.open_positions, 1)
        self.assertAlmostEqual(state.instrument_exposure_pct, (750.0 / 2385.0) * 100.0)
        self.assertEqual(
            calls,
            [EtoroDemoBroker.DEMO_PORTFOLIO_URL, EtoroDemoBroker.DEMO_PNL_URL],
        )

    def test_risk_engine_rejects_when_authoritative_daily_pnl_is_absent(self) -> None:
        state = _broker_for_pnl(_documented_pnl_shape()).risk_portfolio_state(spread_pct=0.2)
        state = replace(state, volatility_pct=2.0)
        candidate = TradeCandidate(
            instrument="TEST",
            direction=Direction.LONG,
            confidence=80,
            entry=100.0,
            stop=99.0,
            target_1=102.0,
        )
        proposal = RiskEngine().evaluate(candidate, state, allow_fractional_sizing=True)
        self.assertIs(proposal.risk.status, RiskStatus.REJECT)
        self.assertIn("missing_daily_pnl", proposal.risk.reasons)

    def test_explicit_authoritative_daily_pnl_is_preserved(self) -> None:
        pnl = _documented_pnl_shape()
        pnl["clientPortfolio"]["dailyPnl"] = -42.5
        state = _broker_for_pnl(pnl).risk_portfolio_state(spread_pct=0.2)
        self.assertEqual(state.daily_pnl, -42.5)

    def test_missing_pending_order_collection_fails_closed(self) -> None:
        pnl = _documented_pnl_shape()
        pnl["clientPortfolio"].pop("orders")
        broker = _broker_for_pnl(pnl)
        with self.assertRaisesRegex(RuntimeError, "missing valid orders"):
            broker.risk_portfolio_state(spread_pct=0.2)

    def test_negative_pending_order_amount_fails_closed(self) -> None:
        pnl = _documented_pnl_shape()
        pnl["clientPortfolio"]["orders"][0]["amount"] = -150.0
        broker = _broker_for_pnl(pnl)
        with self.assertRaisesRegex(RuntimeError, "negative amount"):
            broker.risk_portfolio_state(spread_pct=0.2)

    def test_negative_manual_open_order_amount_fails_closed(self) -> None:
        pnl = _documented_pnl_shape()
        pnl["clientPortfolio"]["ordersForOpen"][0]["amount"] = -200.0
        broker = _broker_for_pnl(pnl)
        with self.assertRaisesRegex(RuntimeError, "negative amount"):
            broker.risk_portfolio_state(spread_pct=0.2)

    def test_nested_pnl_formula_is_used_when_total_unrealized_scalar_is_absent(self) -> None:
        pnl = _documented_pnl_shape()
        pnl["clientPortfolio"].pop("unrealizedPnL")
        state = _broker_for_pnl(pnl).risk_portfolio_state(spread_pct=0.2)
        # 50 - 20 + 30 + 15 + 50 closed mirror profit = 125.
        self.assertIsNone(state.daily_pnl)
        self.assertEqual(state.equity, 2335.0)


if __name__ == "__main__":
    unittest.main()
