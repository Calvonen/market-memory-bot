from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.etoro_session_observability import trading_session_state_from_etoro_update


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def update(
    *,
    is_market_open: bool | None,
    is_exchange_open: bool | None,
    timestamp: datetime | None = NOW,
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


class EtoroSessionObservabilityTests(unittest.TestCase):
    def test_exchange_open_maps_without_extended_hours(self) -> None:
        state = trading_session_state_from_etoro_update(
            update(is_market_open=True, is_exchange_open=True),
            now=NOW,
            max_age=timedelta(seconds=30),
            allow_extended_hours=False,
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertTrue(state.exchange_session_open)
        self.assertFalse(state.broker_extended_session_available)
        self.assertTrue(state.execution_observable)

    def test_broker_open_exchange_closed_is_direct_extended_session_evidence(self) -> None:
        state = trading_session_state_from_etoro_update(
            update(is_market_open=True, is_exchange_open=False),
            now=NOW,
            max_age=timedelta(seconds=30),
            allow_extended_hours=True,
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertTrue(state.broker_extended_session_available)
        self.assertTrue(state.uses_extended_hours)
        self.assertTrue(state.execution_observable)

    def test_extended_session_requires_explicit_marketai_policy(self) -> None:
        state = trading_session_state_from_etoro_update(
            update(is_market_open=True, is_exchange_open=False),
            now=NOW,
            max_age=timedelta(seconds=30),
            allow_extended_hours=False,
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertTrue(state.broker_extended_session_available)
        self.assertFalse(state.execution_observable)
        self.assertFalse(state.uses_extended_hours)

    def test_stale_or_future_data_never_becomes_observable(self) -> None:
        for timestamp in (
            NOW - timedelta(seconds=31),
            NOW + timedelta(microseconds=1),
        ):
            with self.subTest(timestamp=timestamp):
                state = trading_session_state_from_etoro_update(
                    update(
                        is_market_open=True,
                        is_exchange_open=False,
                        timestamp=timestamp,
                    ),
                    now=NOW,
                    max_age=timedelta(seconds=30),
                    allow_extended_hours=True,
                )
                self.assertIsNotNone(state)
                assert state is not None
                self.assertFalse(state.market_data_fresh)
                self.assertFalse(state.execution_observable)

    def test_missing_or_contradictory_session_evidence_fails_closed(self) -> None:
        cases = (
            update(is_market_open=None, is_exchange_open=False),
            update(is_market_open=True, is_exchange_open=None),
            update(is_market_open=True, is_exchange_open=False, timestamp=None),
            update(is_market_open=False, is_exchange_open=True),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertIsNone(
                    trading_session_state_from_etoro_update(
                        candidate,
                        now=NOW,
                        max_age=timedelta(seconds=30),
                        allow_extended_hours=True,
                    )
                )

    def test_invalid_mapper_inputs_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_age"):
            trading_session_state_from_etoro_update(
                update(is_market_open=True, is_exchange_open=True),
                now=NOW,
                max_age=timedelta(0),
                allow_extended_hours=False,
            )
        with self.assertRaisesRegex(ValueError, "now"):
            trading_session_state_from_etoro_update(
                update(is_market_open=True, is_exchange_open=True),
                now=datetime(2026, 9, 5, 12, 0),
                max_age=timedelta(seconds=30),
                allow_extended_hours=False,
            )


if __name__ == "__main__":
    unittest.main()
