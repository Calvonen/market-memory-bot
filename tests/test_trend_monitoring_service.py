import asyncio
import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.tracked_candle_pipeline import TrackedCandlePipeline
from trading_system.tracked_etoro_live import TrackedEtoroMarketUpdate
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.trend_monitoring_runtime import TrendMonitoringRuntime
from trading_system.trend_monitoring_service import (
    _align_candle_pipeline,
    stream_supervised_trend_monitoring,
)
from trading_system.trend_monitoring_supervisor import TrendMonitoringSupervisor
from trading_system.trend_monitoring_targets import _selected_targets


_END = object()


def target(identifier: str, etoro_id: int, *, instrument: str | None = None) -> TrackedEtoroInstrument:
    ticker = instrument or identifier.upper()
    return TrackedEtoroInstrument(
        tracked_instrument_id=identifier,
        instrument=ticker,
        market="USA",
        etoro_instrument_id=etoro_id,
        etoro_symbol=ticker,
        etoro_display_name=f"{ticker} Inc",
        etoro_market="NASDAQ",
    )


def snapshot(*items: TrackedEtoroInstrument):
    return _selected_targets(resolved=tuple(items), unresolved_tracked_instrument_ids=())


def seed_pipeline(pipeline: TrackedCandlePipeline, item: TrackedEtoroInstrument) -> None:
    price = Decimal("100")
    pipeline.add(
        TrackedEtoroMarketUpdate(
            tracked_instrument_id=item.tracked_instrument_id,
            instrument=item.instrument,
            market=item.market,
            etoro_instrument_id=item.etoro_instrument_id,
            update=EtoroMarketUpdate(
                instrument_id=item.etoro_instrument_id,
                bid=price,
                ask=price,
                last_execution=price,
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                is_market_open=True,
                is_exchange_open=True,
                message_type="Update",
            ),
        )
    )


class FakeStream:
    def __init__(self, first_item: object) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        self.queue.put_nowait(first_item)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is _END:
            raise StopAsyncIteration
        return item

    async def aclose(self) -> None:
        self.closed = True


class ControlledSleep:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[None] = asyncio.Queue()

    async def __call__(self, _seconds: float) -> None:
        await self.queue.get()

    def trigger(self) -> None:
        self.queue.put_nowait(None)


class TrendMonitoringServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_candle_alignment_discards_same_id_with_changed_identity(self) -> None:
        pipeline = TrackedCandlePipeline()
        old = target("a", 101, instrument="AAA")
        changed = target("a", 999, instrument="AAA.NEW")
        seed_pipeline(pipeline, old)

        discarded = _align_candle_pipeline(pipeline, snapshot(changed))

        self.assertEqual(discarded, ("a",))
        self.assertIsNone(pipeline.tracked_identity("a"))

    async def test_changed_snapshot_closes_old_stream_and_prunes_candle_state_before_replacement(self) -> None:
        runtime = TrendMonitoringRuntime()
        pipeline = TrackedCandlePipeline()
        a = target("a", 101)
        b = target("b", 202)
        seed_pipeline(pipeline, a)

        current = [snapshot(a)]
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: current[0], runtime=runtime)
        sleep = ControlledSleep()
        streams: list[FakeStream] = []

        def stream_factory(targets):
            stream = FakeStream(targets.resolved[0].tracked_instrument_id)
            streams.append(stream)
            return stream

        service = stream_supervised_trend_monitoring(
            supervisor=supervisor,
            candle_pipeline=pipeline,
            stream_factory=stream_factory,
            refresh_interval_seconds=30,
            sleep=sleep,
        )

        self.assertEqual(await anext(service), "a")
        self.assertEqual(pipeline.tracked_identity("a"), ("A", "USA", 101))

        current[0] = snapshot(b)
        sleep.trigger()
        self.assertEqual(await asyncio.wait_for(anext(service), timeout=1), "b")

        self.assertTrue(streams[0].closed)
        self.assertFalse(streams[1].closed)
        self.assertIsNone(pipeline.tracked_identity("a"))
        await service.aclose()
        self.assertTrue(streams[1].closed)

    async def test_unchanged_snapshot_does_not_restart_stream(self) -> None:
        runtime = TrendMonitoringRuntime()
        pipeline = TrackedCandlePipeline()
        a = target("a", 101)
        current = snapshot(a)
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: current, runtime=runtime)
        sleep = ControlledSleep()
        streams: list[FakeStream] = []

        def stream_factory(_targets):
            stream = FakeStream("first")
            streams.append(stream)
            return stream

        service = stream_supervised_trend_monitoring(
            supervisor=supervisor,
            candle_pipeline=pipeline,
            stream_factory=stream_factory,
            refresh_interval_seconds=30,
            sleep=sleep,
        )

        self.assertEqual(await anext(service), "first")
        streams[0].queue.put_nowait("second")
        sleep.trigger()
        self.assertEqual(await asyncio.wait_for(anext(service), timeout=1), "second")
        self.assertEqual(len(streams), 1)
        self.assertFalse(streams[0].closed)
        await service.aclose()

    async def test_empty_snapshot_waits_until_refresh_without_opening_stream(self) -> None:
        runtime = TrendMonitoringRuntime()
        pipeline = TrackedCandlePipeline()
        a = target("a", 101)
        current = [snapshot()]
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: current[0], runtime=runtime)
        sleep = ControlledSleep()
        streams: list[FakeStream] = []

        def stream_factory(targets):
            stream = FakeStream(targets.resolved[0].tracked_instrument_id)
            streams.append(stream)
            return stream

        service = stream_supervised_trend_monitoring(
            supervisor=supervisor,
            candle_pipeline=pipeline,
            stream_factory=stream_factory,
            refresh_interval_seconds=30,
            sleep=sleep,
        )
        pending = asyncio.create_task(anext(service))
        await asyncio.sleep(0)
        self.assertEqual(streams, [])

        current[0] = snapshot(a)
        sleep.trigger()
        self.assertEqual(await asyncio.wait_for(pending, timeout=1), "a")
        self.assertEqual(len(streams), 1)
        await service.aclose()

    async def test_nonpositive_refresh_interval_fails_before_selection(self) -> None:
        runtime = TrendMonitoringRuntime()
        pipeline = TrackedCandlePipeline()
        called = False

        def select_targets():
            nonlocal called
            called = True
            return snapshot()

        supervisor = TrendMonitoringSupervisor(select_targets=select_targets, runtime=runtime)
        service = stream_supervised_trend_monitoring(
            supervisor=supervisor,
            candle_pipeline=pipeline,
            stream_factory=lambda _targets: FakeStream("unused"),
            refresh_interval_seconds=0,
        )
        with self.assertRaisesRegex(ValueError, "must be positive"):
            await anext(service)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
