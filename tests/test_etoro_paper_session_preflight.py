from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_system.brokers.etoro_demo import EtoroDemoBroker
from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.etoro_paper_session_preflight import verify_etoro_demo_session_execution


NOW = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)


def update(*, market_open: bool | None, exchange_open: bool | None, age_seconds: int = 0):
    return EtoroMarketUpdate(
        instrument_id=123,
        bid=Decimal("100"),
        ask=Decimal("100.1"),
        last_execution=Decimal("100.05"),
        timestamp=NOW - timedelta(seconds=age_seconds),
        is_market_open=market_open,
        is_exchange_open=exchange_open,
        message_type="Update",
    )


class Provider:
    def __init__(self, *updates: EtoroMarketUpdate) -> None:
        self.updates = updates
        self.calls: list[tuple[int, bool]] = []

    async def stream_instrument(self, instrument_id: int, *, reconnect: bool = True):
        self.calls.append((instrument_id, reconnect))
        for item in self.updates:
            yield item


def broker() -> EtoroDemoBroker:
    return EtoroDemoBroker(
        api_key="api",
        user_key="user",
        instrument_id=123,
        amount_usd=100,
        timeout_seconds=1,
    )


class EtoroPaperSessionPreflightTests(unittest.TestCase):
    def test_fresh_regular_exchange_session_is_allowed(self) -> None:
        provider = Provider(update(market_open=True, exchange_open=True))
        verify_etoro_demo_session_execution(
            provider,
            broker(),
            now=lambda: NOW,
            timeout_seconds=1,
        )
        self.assertEqual(provider.calls, [(123, False)])

    def test_extended_session_is_blocked_without_verified_order_support(self) -> None:
        provider = Provider(update(market_open=True, exchange_open=False))
        with self.assertRaisesRegex(RuntimeError, "session preflight blocked execution"):
            verify_etoro_demo_session_execution(
                provider,
                broker(),
                now=lambda: NOW,
                timeout_seconds=1,
            )

    def test_stale_session_evidence_is_blocked(self) -> None:
        provider = Provider(update(market_open=True, exchange_open=True, age_seconds=31))
        with self.assertRaisesRegex(RuntimeError, "session preflight blocked execution"):
            verify_etoro_demo_session_execution(
                provider,
                broker(),
                now=lambda: NOW,
                timeout_seconds=1,
                max_age=timedelta(seconds=30),
            )

    def test_missing_session_flags_fail_closed_when_stream_ends(self) -> None:
        provider = Provider(update(market_open=None, exchange_open=None))
        with self.assertRaisesRegex(RuntimeError, "without explicit session evidence"):
            verify_etoro_demo_session_execution(
                provider,
                broker(),
                now=lambda: NOW,
                timeout_seconds=1,
            )

    def test_contradictory_session_evidence_is_not_accepted(self) -> None:
        provider = Provider(update(market_open=False, exchange_open=True))
        with self.assertRaisesRegex(RuntimeError, "without explicit session evidence"):
            verify_etoro_demo_session_execution(
                provider,
                broker(),
                now=lambda: NOW,
                timeout_seconds=1,
            )


if __name__ == "__main__":
    unittest.main()
