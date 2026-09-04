import asyncio
import unittest

import requests

from trading_system.tracked_candle_pipeline import TrackedCandlePipeline
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.trend_monitoring_runtime import TrendMonitoringRuntime
from trading_system.trend_monitoring_service import (
    _is_transient_refresh_error,
    stream_supervised_trend_monitoring,
)
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
    def __init__(self, first_item: object, *, close_error: Exception | None = None) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        self.queue.put_nowait(first_item)
        self.closed = False
        self.close_error = close_error

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
        if self.close_error is not None:
            raise self.close_error


class ControlledSleep:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[None] = asyncio.Queue()

    async def __call__(self, _seconds: float) -> None:
        await self.queue.get()

    def trigger(self) -> None:
        self.queue.put_nowait(None)


class TrendMonitoringServiceFailureTests(unittest.IsolatedAsyncioTestCase):
    def test_json_decode_failure_is_not_transient_transport_error(self) -> None:
        decode_error = requests.exceptions.JSONDecodeError("bad json", "{", 0)
        try:
            raise decode_error
        except requests.RequestException as exc:
            wrapped = RuntimeError("eToro instrument search returned invalid JSON")
            wrapped.__cause__ = exc

        self.assertFalse(_is_transient_refresh_error(wrapped))

    async def test_initial_refresh_failure_still_propagates(self) -> None:
        runtime = TrendMonitoringRuntime()
        pipeline = TrackedCandlePipeline()

        def select_targets():
            raise RuntimeError("initial refresh failed")

        supervisor = TrendMonitoringSupervisor(select_targets=select_targets, runtime=runtime)
        service = stream_supervised_trend_monitoring(
            supervisor=supervisor,
            candle_pipeline=pipeline,
            stream_factory=lambda _targets: FailureStream("unused"),
            refresh_interval_seconds=30,
        )

        with self.assertRaisesRegex(RuntimeError, "initial refresh failed"):
            await anext(service)

    async def test_periodic_transport_failure_keeps_existing_stream_and_retries(self) -> None:
        runtime = TrendMonitoringRuntime()
        pipeline = TrackedCandlePipeline()
        a = target("a", 101)
        calls = 0

        def select_targets():
            nonlocal calls
            calls += 1
            if calls == 2:
                try:
                    raise requests.ReadTimeout("search timed out")
                except requests.RequestException as exc:
                    raise RuntimeError("eToro instrument search failed") from exc
            return snapshot(a)

        supervisor = TrendMonitoringSupervisor(select_targets=select_targets, runtime=runtime)
        sleep = ControlledSleep()
        streams: list[FailureStream] = []

        def stream_factory(_targets):
            stream = FailureStream("first")
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
        with self.assertLogs("trading_system.trend_monitoring_service", level="ERROR") as logs:
            self.assertEqual(await asyncio.wait_for(anext(service), timeout=1), "second")

        self.assertEqual(calls, 2)
        self.assertEqual(len(streams), 1)
        self.assertFalse(streams[0].closed)
        self.assertIn("transient transport failure", "\n".join(logs.output))

        streams[0].queue.put_nowait("third")
        sleep.trigger()
        self.assertEqual(await asyncio.wait_for(anext(service), timeout=1), "third")
        self.assertEqual(calls, 3)
        self.assertEqual(len(streams), 1)
        self.assertFalse(streams[0].closed)
        await service.aclose()
        self.assertTrue(streams[0].closed)

    async def test_periodic_validation_failure_propagates_fail_closed(self) -> None:
        runtime = TrendMonitoringRuntime()
        pipeline = TrackedCandlePipeline()
        a = target("a", 101)
        calls = 0
        streams: list[FailureStream] = []

        def select_targets():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("duplicate active tracked instrument id")
            return snapshot(a)

        def stream_factory(_targets):
            stream = FailureStream("first")
            streams.append(stream)
            return stream

        supervisor = TrendMonitoringSupervisor(select_targets=select_targets, runtime=runtime)
        sleep = ControlledSleep()
        service = stream_supervised_trend_monitoring(
            supervisor=supervisor,
            candle_pipeline=pipeline,
            stream_factory=stream_factory,
            refresh_interval_seconds=30,
            sleep=sleep,
        )

        self.assertEqual(await anext(service), "first")
        sleep.trigger()
        with self.assertRaisesRegex(RuntimeError, "duplicate active tracked instrument id"):
            await asyncio.wait_for(anext(service), timeout=1)

        self.assertEqual(calls, 2)
        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].closed)

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

    async def test_completed_provider_failure_wins_over_close_failure(self) -> None:
        runtime = TrendMonitoringRuntime()
        pipeline = TrackedCandlePipeline()
        a = target("a", 101)
        b = target("b", 202)
        current = [snapshot(a)]
        supervisor = TrendMonitoringSupervisor(select_targets=lambda: current[0], runtime=runtime)
        sleep = ControlledSleep()
        streams: list[FailureStream] = []

        def stream_factory(targets):
            stream = FailureStream(
                targets.resolved[0].tracked_instrument_id,
                close_error=RuntimeError("close failed"),
            )
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
