from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.tracked_etoro_orchestrator import stream_tracked_etoro_instruments
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


def _tracked(
    tracked_id: str,
    etoro_id: int,
    instrument: str,
    market: str = "",
) -> TrackedEtoroInstrument:
    return TrackedEtoroInstrument(
        tracked_instrument_id=tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_id,
        etoro_symbol=instrument,
        etoro_display_name=instrument,
    )


def _update(instrument_id: int, price: str) -> EtoroMarketUpdate:
    value = Decimal(price)
    return EtoroMarketUpdate(
        instrument_id=instrument_id,
        bid=value,
        ask=value,
        last_execution=value,
        timestamp=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        is_market_open=True,
        is_exchange_open=True,
        message_type="Update",
    )


class StubProvider:
    def __init__(self, streams: dict[int, list[EtoroMarketUpdate] | Exception]) -> None:
        self.streams = streams
        self.calls: list[tuple[int, bool]] = []

    async def stream_instrument(self, instrument_id: int, *, reconnect: bool = True):
        self.calls.append((instrument_id, reconnect))
        configured = self.streams[instrument_id]
        if isinstance(configured, Exception):
            raise configured
        for update in configured:
            await asyncio.sleep(0)
            yield update


class TrackedEtoroOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_orchestrator_merges_multiple_tracked_streams_and_preserves_identity(self) -> None:
        first = _tracked("tracked-1", 101, "AAA", "LSE")
        second = _tracked("tracked-2", 202, "BBB", "NASDAQ")
        provider = StubProvider(
            {
                101: [_update(101, "10")],
                202: [_update(202, "20")],
            }
        )

        received = [
            item
            async for item in stream_tracked_etoro_instruments(
                (first, second),
                provider,
                reconnect=False,
            )
        ]

        self.assertEqual(
            {(item.tracked_instrument_id, item.etoro_instrument_id) for item in received},
            {("tracked-1", 101), ("tracked-2", 202)},
        )
        self.assertEqual(
            {(item.instrument, item.market) for item in received},
            {("AAA", "LSE"), ("BBB", "NASDAQ")},
        )
        self.assertEqual(set(provider.calls), {(101, False), (202, False)})

    async def test_orchestrator_accepts_empty_input_without_starting_streams(self) -> None:
        provider = StubProvider({})

        received = [item async for item in stream_tracked_etoro_instruments((), provider)]

        self.assertEqual(received, [])
        self.assertEqual(provider.calls, [])

    async def test_duplicate_tracked_identity_fails_closed_before_streaming(self) -> None:
        provider = StubProvider({101: [], 202: []})
        first = _tracked("same", 101, "AAA")
        second = _tracked("same", 202, "BBB")

        with self.assertRaisesRegex(ValueError, "duplicate tracked_instrument_id"):
            _ = [item async for item in stream_tracked_etoro_instruments((first, second), provider)]

        self.assertEqual(provider.calls, [])

    async def test_duplicate_etoro_identity_fails_closed_before_streaming(self) -> None:
        provider = StubProvider({101: []})
        first = _tracked("tracked-1", 101, "AAA")
        second = _tracked("tracked-2", 101, "AAA.L")

        with self.assertRaisesRegex(ValueError, "duplicate etoro_instrument_id"):
            _ = [item async for item in stream_tracked_etoro_instruments((first, second), provider)]

        self.assertEqual(provider.calls, [])

    async def test_worker_failure_is_propagated_and_sibling_streams_are_cancelled(self) -> None:
        failed = _tracked("tracked-1", 101, "AAA")
        sibling = _tracked("tracked-2", 202, "BBB")

        class FailingProvider(StubProvider):
            def __init__(self) -> None:
                super().__init__({101: RuntimeError("stream failed"), 202: []})
                self.sibling_cancelled = False

            async def stream_instrument(self, instrument_id: int, *, reconnect: bool = True):
                self.calls.append((instrument_id, reconnect))
                if instrument_id == 101:
                    await asyncio.sleep(0)
                    raise RuntimeError("stream failed")
                try:
                    while True:
                        await asyncio.sleep(3600)
                        if False:
                            yield _update(202, "20")
                finally:
                    self.sibling_cancelled = True

        provider = FailingProvider()

        with self.assertRaisesRegex(RuntimeError, "stream failed"):
            _ = [item async for item in stream_tracked_etoro_instruments((failed, sibling), provider)]

        self.assertEqual(set(provider.calls), {(101, True), (202, True)})
        self.assertTrue(provider.sibling_cancelled)

    async def test_bounded_queue_applies_backpressure_to_fast_producer(self) -> None:
        total_updates = 50
        tracked = _tracked("tracked-1", 101, "AAA")
        produced: list[int] = []

        class BurstProvider(StubProvider):
            async def stream_instrument(self, instrument_id: int, *, reconnect: bool = True):
                self.calls.append((instrument_id, reconnect))
                for i in range(total_updates):
                    produced.append(i)
                    yield _update(instrument_id, str(i))

        provider = BurstProvider({})

        stream = stream_tracked_etoro_instruments(
            (tracked,),
            provider,
            reconnect=False,
            queue_maxsize=2,
        )

        first = await stream.__anext__()

        # Let the producer race ahead without any further consumption. With
        # no queue bound it would race through every remaining update in
        # this same burst; with a bounded queue and nothing draining it,
        # the producer can only ever get a couple of items ahead before its
        # own `queue.put` blocks, no matter how many scheduling turns pass.
        for _ in range(10):
            await asyncio.sleep(0)

        self.assertLess(len(produced), total_updates)

        received = [first]
        async for item in stream:
            received.append(item)

        self.assertEqual(len(received), total_updates)
        self.assertEqual(len(produced), total_updates)
        self.assertEqual(
            [item.update.last_execution for item in received],
            [Decimal(str(i)) for i in range(total_updates)],
        )


if __name__ == "__main__":
    unittest.main()
