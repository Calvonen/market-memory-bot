from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from trading_system.trend_monitoring_runtime import TrendRuntimeResult
from trading_system.trend_monitoring_wiring import build_trend_monitoring_service_from_env


def _result_payload(result: TrendRuntimeResult) -> dict[str, Any]:
    """Return one journal-friendly observation without creating persistence."""
    transition = result.transition
    observation = result.observation
    candle = result.candle
    return {
        "type": "trend_observation",
        "tracked_instrument_id": result.tracked_instrument_id,
        "instrument": candle.instrument,
        "market": candle.market,
        "etoro_instrument_id": candle.etoro_instrument_id,
        "interval_minutes": candle.interval_minutes,
        "candle_start": candle.start.isoformat(),
        "candle_close": str(candle.close),
        "source_minutes": candle.source_minutes,
        "ready": observation.ready,
        "candidate_state": observation.candidate_state.value,
        "reason": observation.reason,
        "confirmed_state": transition.state.value,
        "changed": transition.changed,
        "pending_candidate": (
            transition.pending_candidate.value
            if transition.pending_candidate is not None
            else None
        ),
        "pending_count": transition.pending_count,
    }


async def run_trend_monitoring_worker(
    *,
    service=None,
    emit: Callable[[str], None] = print,
) -> int:
    """Consume the production Trend stream and expose observations via stdout only.

    The worker intentionally has no database/event/trading writer. Journald/stdout
    is the first test surface for the single 15-minute Trend model. The supplied
    service is closed on cooperative cancellation or normal termination.
    """
    stream = service or build_trend_monitoring_service_from_env()
    try:
        async for batch in stream:
            for result in batch.trend_results:
                emit(json.dumps(_result_payload(result), sort_keys=True))
    finally:
        await stream.aclose()
    return 0


def main() -> int:
    return asyncio.run(run_trend_monitoring_worker())


if __name__ == "__main__":
    raise SystemExit(main())
