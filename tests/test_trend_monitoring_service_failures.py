import asyncio
import unittest

from trading_system.tracked_candle_pipeline import TrackedCandlePipeline
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.trend_monitoring_runtime import TrendMonitoringRuntime
from trading_system.trend_monitoring_service import stream_supervised_trend_monitoring
from trading_system.trend_monitoring_supervisor import TrendMonitoringSupervisor
from trading_system.trend_monitoring_targets import _selected_targets


_END = object()


def target(identifier: str, etoro_id: int) -> TrackedEtoroInstrument:
    ticker = identifier.upper()
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


class FailureStream:
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
        if isinstance(item, BaseException):
            raise item
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


class TrendMonitoringServiceFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_refresh_does_not_hide_completed_provider_failure(self) -> None:
        runtime = TrendMonitoringRuntime()
        pipeline = TrackedCandlePipeline()
        a = target("a", 101)
        b = target("b", 202)
        current = [snapshot(a)]
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: current[0], runtime=runtime)
        sleep = ControlledSleep()
        streams: list[FailureStream] = []

        def stream_factory(targets):
            stream = FailureStream(targets.resolved[0].tracked_instrument_id)
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
        streams[0].queue.put_nowait(RuntimeError("provider failed"))
        current[0] = snapshot(b)
        sleep.trigger()
        await asyncio.sleep(0)

        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            await anext(service)
        self.assertTrue(streams[0].closed)
        self.assertEqual(len(streams), 1)

    async def test_changed_refresh_does_not_hide_completed_stream_end(self) -> None:
        runtime = TrendMonitoringRuntime()
        pipeline = TrackedCandlePipeline()
        a = target("a", 101)
        b = target("b", 202)
        current = [snapshot(a)]
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: current[0], runtime=runtime)
        sleep = ControlledSleep()
        streams: list[FailureStream] = []

        def stream_factory(targets):
            stream = FailureStream(targets.resolved[0].tracked_instrument_id)
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
        streams[0].queue.put_nowait(_END)
        current[0] = snapshot(b)
        sleep.trigger()
        await asyncio.sleep(0)

        with self.assertRaisesRegex(RuntimeError, "Trend live stream ended unexpectedly"):
            await anext(service)
        self.assertTrue(streams[0].closed)
        self.assertEqual(len(streams), 1)


if __name__ == "__main__":
    unittest.main()
