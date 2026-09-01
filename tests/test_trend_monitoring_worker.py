import json
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from trading_system.trend_monitoring_contract import TrendState
from trading_system.trend_monitoring_worker import _result_payload, run_trend_monitoring_worker


class FakeService:
    def __init__(self, batches):
        self._batches = iter(batches)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._batches)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def aclose(self):
        self.closed = True


def fake_result():
    candle = SimpleNamespace(
        instrument="AAA",
        market="USA",
        etoro_instrument_id=101,
        interval_minutes=15,
        start=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        close=Decimal("101.25"),
        source_minutes=15,
    )
    observation = SimpleNamespace(
        ready=True,
        candidate_state=TrendState.BULLISH,
        reason="price_above_rising_ema50_above_ema200",
    )
    transition = SimpleNamespace(
        state=TrendState.BULLISH,
        changed=True,
        pending_candidate=None,
        pending_count=0,
    )
    return SimpleNamespace(
        tracked_instrument_id="tracked-1",
        candle=candle,
        observation=observation,
        transition=transition,
    )


class TrendMonitoringWorkerTests(unittest.IsolatedAsyncioTestCase):
    def test_result_payload_is_observation_only_and_serializable(self):
        payload = _result_payload(fake_result())

        self.assertEqual(payload["type"], "trend_observation")
        self.assertEqual(payload["tracked_instrument_id"], "tracked-1")
        self.assertEqual(payload["confirmed_state"], "bullish")
        self.assertEqual(payload["candle_close"], "101.25")
        json.dumps(payload)

    async def test_worker_emits_trend_results_and_closes_service(self):
        service = FakeService(
            [
                SimpleNamespace(trend_results=()),
                SimpleNamespace(trend_results=(fake_result(),)),
            ]
        )
        emitted = []

        result = await run_trend_monitoring_worker(service=service, emit=emitted.append)

        self.assertEqual(result, 0)
        self.assertTrue(service.closed)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(json.loads(emitted[0])["confirmed_state"], "bullish")

    async def test_default_stdout_emitter_flushes_each_observation(self):
        service = FakeService([SimpleNamespace(trend_results=(fake_result(),))])

        with patch("builtins.print") as mocked_print:
            result = await run_trend_monitoring_worker(service=service)

        self.assertEqual(result, 0)
        self.assertTrue(service.closed)
        mocked_print.assert_called_once()
        line = mocked_print.call_args.args[0]
        self.assertEqual(json.loads(line)["confirmed_state"], "bullish")
        self.assertEqual(mocked_print.call_args.kwargs, {"flush": True})


if __name__ == "__main__":
    unittest.main()
