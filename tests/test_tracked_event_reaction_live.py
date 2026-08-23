from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import trading_system.tracked_event_reaction_live as live_module
from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_etoro_live import TrackedEtoroMarketUpdate
from trading_system.tracked_event_reaction_live import stream_tracked_event_reaction_runtime
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


TRACKED = TrackedEtoroInstrument(
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    etoro_instrument_id=123,
    etoro_symbol="ABC.L",
    etoro_display_name="ABC plc",
)


def _update(minute: int) -> TrackedEtoroMarketUpdate:
    return TrackedEtoroMarketUpdate(
        tracked_instrument_id="tracked-1",
        instrument="ABC",
        market="LSE",
        etoro_instrument_id=123,
        update=EtoroMarketUpdate(
            instrument_id=123,
            bid=Decimal("100"),
            ask=Decimal("100.1"),
            last_execution=Decimal("100.05"),
            timestamp=datetime(2026, 8, 23, 7, minute, tzinfo=UTC),
            is_market_open=True,
            is_exchange_open=True,
            message_type="rate",
        ),
    )


def _candle(minute: int) -> TrackedMarketCandle:
    price = Decimal("100")
    return TrackedMarketCandle(
        tracked_instrument_id="tracked-1",
        instrument="ABC",
        market="LSE",
        etoro_instrument_id=123,
        interval_minutes=1,
        start=datetime(2026, 8, 23, 7, minute, tzinfo=UTC),
        open=price,
        high=price,
        low=price,
        close=price,
        source_minutes=1,
    )


class _FakeRuntime:
    def __init__(self, batches: tuple[tuple[TrackedMarketCandle, ...], ...]) -> None:
        self._batches = list(batches)
        self.seen: list[TrackedEtoroMarketUpdate] = []

    def add_market_update(self, item: TrackedEtoroMarketUpdate) -> tuple[TrackedMarketCandle, ...]:
        self.seen.append(item)
        return self._batches.pop(0)


class TrackedEventReactionLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_live_update_is_fed_once_and_exact_batch_is_yielded(self) -> None:
        first = _update(5)
        second = _update(6)
        closed = (_candle(5),)
        runtime = _FakeRuntime(((), closed))
        calls: list[tuple[object, object, bool, int]] = []

        async def fake_stream(tracked_instruments, provider, *, reconnect, queue_maxsize):
            calls.append((tuple(tracked_instruments), provider, reconnect, queue_maxsize))
            yield first
            yield second

        provider = object()
        with patch.object(live_module, "stream_tracked_etoro_instruments", fake_stream):
            results = [
                item
                async for item in stream_tracked_event_reaction_runtime(
                    (TRACKED,),
                    provider,
                    runtime,  # type: ignore[arg-type]
                    reconnect=False,
                    queue_maxsize=7,
                )
            ]

        self.assertEqual(runtime.seen, [first, second])
        self.assertEqual([item.update for item in results], [first, second])
        self.assertEqual([item.candles for item in results], [(), closed])
        self.assertEqual(calls, [((TRACKED,), provider, False, 7)])

    async def test_stream_failure_propagates_without_synthetic_output(self) -> None:
        first = _update(5)
        runtime = _FakeRuntime(((),))

        async def failing_stream(tracked_instruments, provider, *, reconnect, queue_maxsize):
            yield first
            raise RuntimeError("stream failed")

        with patch.object(live_module, "stream_tracked_etoro_instruments", failing_stream):
            stream = stream_tracked_event_reaction_runtime(
                (TRACKED,),
                object(),
                runtime,  # type: ignore[arg-type]
            )
            first_result = await anext(stream)
            self.assertEqual(first_result.update, first)
            with self.assertRaisesRegex(RuntimeError, "stream failed"):
                await anext(stream)

        self.assertEqual(runtime.seen, [first])


if __name__ == "__main__":
    unittest.main()
