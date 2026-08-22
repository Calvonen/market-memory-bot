from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.tracked_etoro_live import stream_tracked_etoro_instrument
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


class StubStreamProvider:
    def __init__(self, updates: tuple[EtoroMarketUpdate, ...]) -> None:
        self.updates = updates
        self.calls: list[tuple[int, bool]] = []

    async def stream_instrument(self, instrument_id: int, *, reconnect: bool = True):
        self.calls.append((instrument_id, reconnect))
        for update in self.updates:
            yield update


def _tracked() -> TrackedEtoroInstrument:
    return TrackedEtoroInstrument(
        tracked_instrument_id="tracked-1",
        instrument="NOKIA.HE",
        market="Helsinki",
        etoro_instrument_id=4242,
        etoro_symbol="NOKIA",
        etoro_display_name="Nokia Oyj",
    )


def _update(instrument_id: int, price: str = "10.5") -> EtoroMarketUpdate:
    value = Decimal(price)
    return EtoroMarketUpdate(
        instrument_id=instrument_id,
        bid=value - Decimal("0.01"),
        ask=value + Decimal("0.01"),
        last_execution=value,
        timestamp=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        is_market_open=True,
        is_exchange_open=True,
        message_type="Update",
    )


async def _collect(
    tracked: TrackedEtoroInstrument,
    provider: StubStreamProvider,
    *,
    reconnect: bool = True,
):
    return [
        item
        async for item in stream_tracked_etoro_instrument(
            tracked,
            provider,
            reconnect=reconnect,
        )
    ]


class TrackedEtoroLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_subscribes_with_resolved_etoro_id_and_reconnect_flag(self) -> None:
        provider = StubStreamProvider((_update(4242),))

        items = await _collect(_tracked(), provider, reconnect=False)

        self.assertEqual(provider.calls, [(4242, False)])
        self.assertEqual(len(items), 1)

    async def test_stream_preserves_tracked_identity_around_market_update(self) -> None:
        update = _update(4242)
        provider = StubStreamProvider((update,))

        [item] = await _collect(_tracked(), provider)

        self.assertEqual(item.tracked_instrument_id, "tracked-1")
        self.assertEqual(item.instrument, "NOKIA.HE")
        self.assertEqual(item.market, "Helsinki")
        self.assertEqual(item.etoro_instrument_id, 4242)
        self.assertIs(item.update, update)

    async def test_stream_ignores_unexpected_instrument_updates_fail_closed(self) -> None:
        expected = _update(4242, "10.5")
        unexpected = _update(9999, "99")
        provider = StubStreamProvider((unexpected, expected))

        items = await _collect(_tracked(), provider)

        self.assertEqual([item.update for item in items], [expected])

    async def test_empty_provider_stream_yields_no_tracked_updates(self) -> None:
        provider = StubStreamProvider(())

        items = await _collect(_tracked(), provider)

        self.assertEqual(items, [])
        self.assertEqual(provider.calls, [(4242, True)])


if __name__ == "__main__":
    unittest.main()
