from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from trading_system.approved_paper_etoro_session import read_etoro_session_state
from trading_system.etoro_market_data import EtoroMarketUpdate


class _Provider:
    def __init__(self, updates: list[EtoroMarketUpdate], *, delay: float = 0.0) -> None:
        self.updates = updates
        self.delay = delay

    async def stream_instrument(self, instrument_id: int, *, reconnect: bool = True):
        if self.delay:
            await asyncio.sleep(self.delay)
        for update in self.updates:
            yield update


def _update(
    *,
    timestamp: datetime,
    is_market_open: bool | None,
    is_exchange_open: bool | None,
) -> EtoroMarketUpdate:
    return EtoroMarketUpdate(
        instrument_id=6804,
        bid=None,
        ask=None,
        last_execution=None,
        timestamp=timestamp,
        is_market_open=is_market_open,
        is_exchange_open=is_exchange_open,
        message_type="Update",
    )


class ApprovedPaperEtoroSessionTests(unittest.TestCase):
    def test_regular_session_uses_direct_fresh_etoro_evidence(self) -> None:
        now = datetime.now(UTC)
        state = read_etoro_session_state(
            _Provider(
                [
                    _update(
                        timestamp=now,
                        is_market_open=True,
                        is_exchange_open=True,
                    )
                ]
            ),
            instrument_id=6804,
            timeout_seconds=1.0,
            max_age_seconds=30.0,
            allow_extended_hours=False,
        )
        self.assertTrue(state.exchange_session_open)
        self.assertTrue(state.execution_observable)
        self.assertFalse(state.uses_extended_hours)

    def test_extended_session_remains_policy_blocked(self) -> None:
        now = datetime.now(UTC)
        state = read_etoro_session_state(
            _Provider(
                [
                    _update(
                        timestamp=now,
                        is_market_open=True,
                        is_exchange_open=False,
                    )
                ]
            ),
            instrument_id=6804,
            timeout_seconds=1.0,
            max_age_seconds=30.0,
            allow_extended_hours=False,
        )
        self.assertTrue(state.broker_extended_session_available)
        self.assertFalse(state.execution_observable)

    def test_stale_explicit_evidence_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "stale"):
            read_etoro_session_state(
                _Provider(
                    [
                        _update(
                            timestamp=datetime.now(UTC) - timedelta(minutes=2),
                            is_market_open=True,
                            is_exchange_open=True,
                        )
                    ]
                ),
                instrument_id=6804,
                timeout_seconds=1.0,
                max_age_seconds=30.0,
                allow_extended_hours=False,
            )

    def test_missing_flags_are_not_accepted_as_session_evidence(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ended without explicit"):
            read_etoro_session_state(
                _Provider(
                    [
                        _update(
                            timestamp=datetime.now(UTC),
                            is_market_open=None,
                            is_exchange_open=True,
                        )
                    ]
                ),
                instrument_id=6804,
                timeout_seconds=1.0,
                max_age_seconds=30.0,
                allow_extended_hours=False,
            )

    def test_session_read_timeout_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            read_etoro_session_state(
                _Provider([], delay=0.05),
                instrument_id=6804,
                timeout_seconds=0.001,
                max_age_seconds=30.0,
                allow_extended_hours=False,
            )


if __name__ == "__main__":
    unittest.main()
