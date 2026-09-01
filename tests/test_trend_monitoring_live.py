import unittest
from types import SimpleNamespace
from unittest.mock import patch

from trading_system.trend_monitoring_live import stream_trend_monitoring_runtime


class _Upstream:
    def __init__(self, updates):
        self.updates = list(updates)
        self.closed = False

    def __aiter__(self):
        self._iterator = iter(self.updates)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def aclose(self):
        self.closed = True


class _Pipeline:
    def __init__(self, candles_by_update):
        self.candles_by_update = candles_by_update
        self.updates = []

    def add(self, update):
        self.updates.append(update)
        value = self.candles_by_update[update]
        if isinstance(value, Exception):
            raise value
        return value


class _Runtime:
    def __init__(self, results_by_candle):
        self.results_by_candle = results_by_candle
        self.calls = []

    def add_candle(self, candle, **prerequisites):
        self.calls.append((candle, prerequisites))
        return self.results_by_candle.get(candle)


class TrendMonitoringLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_stream_and_candle_pipeline_feed_runtime_once(self):
        target = SimpleNamespace(tracked_instrument_id="tracked-1", etoro_instrument_id=101)
        update = object()
        one_minute = object()
        fifteen_minute = object()
        trend_result = object()
        upstream = _Upstream([update])
        pipeline = _Pipeline({update: (one_minute, fifteen_minute)})
        runtime = _Runtime({fifteen_minute: trend_result})

        with patch(
            "trading_system.trend_monitoring_live.stream_tracked_etoro_instruments",
            return_value=upstream,
        ) as stream:
            batches = [
                batch
                async for batch in stream_trend_monitoring_runtime(
                    [target],
                    object(),
                    pipeline,
                    runtime,
                    reconnect=False,
                    queue_maxsize=7,
                )
            ]

        self.assertEqual(pipeline.updates, [update])
        self.assertEqual([call[0] for call in runtime.calls], [one_minute, fifteen_minute])
        self.assertTrue(
            all(
                prerequisites
                == {
                    "instrument_active": True,
                    "trend_profile_enabled": True,
                    "etoro_identity_resolved": True,
                }
                for _, prerequisites in runtime.calls
            )
        )
        self.assertEqual(len(batches), 1)
        self.assertIs(batches[0].update, update)
        self.assertEqual(batches[0].candles, (one_minute, fifteen_minute))
        self.assertEqual(batches[0].trend_results, (trend_result,))
        stream.assert_called_once()
        self.assertEqual(stream.call_args.kwargs, {"reconnect": False, "queue_maxsize": 7})
        self.assertTrue(upstream.closed)

    async def test_empty_target_snapshot_does_not_open_upstream(self):
        with patch(
            "trading_system.trend_monitoring_live.stream_tracked_etoro_instruments"
        ) as stream:
            batches = [
                batch
                async for batch in stream_trend_monitoring_runtime(
                    [], object(), _Pipeline({}), _Runtime({})
                )
            ]
        self.assertEqual(batches, [])
        stream.assert_not_called()

    async def test_pipeline_failure_propagates_and_closes_upstream(self):
        target = SimpleNamespace(tracked_instrument_id="tracked-1", etoro_instrument_id=101)
        update = object()
        upstream = _Upstream([update])
        pipeline = _Pipeline({update: RuntimeError("candle failure")})

        with patch(
            "trading_system.trend_monitoring_live.stream_tracked_etoro_instruments",
            return_value=upstream,
        ):
            with self.assertRaisesRegex(RuntimeError, "candle failure"):
                async for _ in stream_trend_monitoring_runtime(
                    [target], object(), pipeline, _Runtime({})
                ):
                    pass

        self.assertTrue(upstream.closed)

    async def test_early_consumer_close_closes_upstream(self):
        target = SimpleNamespace(tracked_instrument_id="tracked-1", etoro_instrument_id=101)
        first_update = object()
        second_update = object()
        upstream = _Upstream([first_update, second_update])
        pipeline = _Pipeline({first_update: (), second_update: ()})

        with patch(
            "trading_system.trend_monitoring_live.stream_tracked_etoro_instruments",
            return_value=upstream,
        ):
            generator = stream_trend_monitoring_runtime(
                [target], object(), pipeline, _Runtime({})
            )
            first = await anext(generator)
            self.assertIs(first.update, first_update)
            await generator.aclose()

        self.assertEqual(pipeline.updates, [first_update])
        self.assertTrue(upstream.closed)


if __name__ == "__main__":
    unittest.main()
