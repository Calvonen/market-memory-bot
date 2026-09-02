import json
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from trading_system.trend_monitoring_contract import TrendState
from trading_system.trend_monitoring_worker import (
    _result_payload,
    _target_snapshot_payload,
    run_trend_monitoring_worker,
)


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


def fake_candle(interval_minutes=15):
    return SimpleNamespace(
        instrument="AAA",
        market="USA",
        etoro_instrument_id=101,
        interval_minutes=interval_minutes,
        start=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        close=Decimal("101.25"),
        source_minutes=interval_minutes,
    )


def fake_result():
    candle = fake_candle()
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

    def test_target_snapshot_payload_contains_only_selection_diagnostics(self):
        resolved = SimpleNamespace(tracked_instrument_id="tracked-1")
        targets = SimpleNamespace(
            resolved=(resolved,),
            unresolved_tracked_instrument_ids=("tracked-2",),
        )

        payload = _target_snapshot_payload(targets)

        self.assertEqual(payload["type"], "trend_monitoring_diagnostic")
        self.assertEqual(payload["status"], "targets_refreshed")
        self.assertEqual(payload["resolved_targets"], 1)
        self.assertEqual(payload["unresolved_targets"], 1)
        self.assertEqual(payload["resolved_tracked_instrument_ids"], ["tracked-1"])
        self.assertEqual(payload["unresolved_tracked_instrument_ids"], ["tracked-2"])

    async def test_worker_emits_pipeline_diagnostics_and_trend_results(self):
        service = FakeService(
            [
                SimpleNamespace(candles=(), trend_results=()),
                SimpleNamespace(
                    candles=(fake_candle(interval_minutes=1),),
                    trend_results=(fake_result(),),
                ),
            ]
        )
        emitted = []

        result = await run_trend_monitoring_worker(service=service, emit=emitted.append)

        self.assertEqual(result, 0)
        self.assertTrue(service.closed)
        payloads = [json.loads(line) for line in emitted]
        self.assertEqual(
            [payload["status"] for payload in payloads if "status" in payload],
            ["market_update_received", "closed_candle_received"],
        )
        self.assertEqual(payloads[-1]["type"], "trend_observation")
        self.assertEqual(payloads[-1]["confirmed_state"], "bullish")

    async def test_pipeline_diagnostic_failure_does_not_abort_stream_or_observation(self):
        service = FakeService(
            [SimpleNamespace(candles=(), trend_results=(fake_result(),))]
        )
        emitted = []

        def flaky_emit(line):
            payload = json.loads(line)
            if payload.get("type") == "trend_monitoring_diagnostic":
                raise BrokenPipeError("diagnostic sink unavailable")
            emitted.append(line)

        result = await run_trend_monitoring_worker(service=service, emit=flaky_emit)

        self.assertEqual(result, 0)
        self.assertTrue(service.closed)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(json.loads(emitted[0])["type"], "trend_observation")

    async def test_default_stdout_emitter_flushes_each_line(self):
        service = FakeService(
            [SimpleNamespace(candles=(), trend_results=(fake_result(),))]
        )

        with patch("builtins.print") as mocked_print:
            result = await run_trend_monitoring_worker(service=service)

        self.assertEqual(result, 0)
        self.assertTrue(service.closed)
        self.assertEqual(mocked_print.call_count, 2)
        payloads = [json.loads(call.args[0]) for call in mocked_print.call_args_list]
        self.assertEqual(payloads[0]["status"], "market_update_received")
        self.assertEqual(payloads[1]["confirmed_state"], "bullish")
        for call in mocked_print.call_args_list:
            self.assertEqual(call.kwargs, {"flush": True})


if __name__ == "__main__":
    unittest.main()
