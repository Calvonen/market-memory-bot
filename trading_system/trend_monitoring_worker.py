from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from trading_system.trend_monitoring_runtime import TrendRuntimeResult
from trading_system.trend_monitoring_targets import TrendMonitoringTargets
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


def _target_snapshot_payload(targets: TrendMonitoringTargets) -> dict[str, Any]:
    """Expose canonical target-selection state without leaking credentials or config."""
    return {
        "type": "trend_monitoring_diagnostic",
        "status": "targets_refreshed",
        "resolved_targets": len(targets.resolved),
        "unresolved_targets": len(targets.unresolved_tracked_instrument_ids),
        "resolved_tracked_instrument_ids": [
            item.tracked_instrument_id for item in targets.resolved
        ],
        "unresolved_tracked_instrument_ids": list(
            targets.unresolved_tracked_instrument_ids
        ),
    }


async def run_trend_monitoring_worker(
    *,
    service=None,
    emit: Callable[[str], None] | None = None,
) -> int:
    """Consume the production Trend stream and expose observations via stdout only.

    The worker intentionally has no database/event/trading writer. Journald/stdout
    is the first test surface for the single 15-minute Trend model. The supplied
    service is closed on cooperative cancellation or normal termination.
    """

    def emit_payload(payload: dict[str, Any]) -> None:
        line = json.dumps(payload, sort_keys=True)
        if emit is None:
            print(line, flush=True)
        else:
            emit(line)

    def emit_diagnostic(payload: dict[str, Any]) -> None:
        """Best-effort diagnostics must never alter monitoring lifecycle."""
        try:
            emit_payload(payload)
        except Exception:
            pass

    if service is None:
        stream = build_trend_monitoring_service_from_env(
            on_target_snapshot=lambda targets: emit_diagnostic(
                _target_snapshot_payload(targets)
            )
        )
    else:
        stream = service

    saw_market_update = False
    saw_closed_candle = False
    try:
        async for batch in stream:
            if not saw_market_update:
                emit_diagnostic(
                    {
                        "type": "trend_monitoring_diagnostic",
                        "status": "market_update_received",
                    }
                )
                saw_market_update = True
            if batch.candles and not saw_closed_candle:
                emit_diagnostic(
                    {
                        "type": "trend_monitoring_diagnostic",
                        "status": "closed_candle_received",
                        "interval_minutes": sorted(
                            {candle.interval_minutes for candle in batch.candles}
                        ),
                    }
                )
                saw_closed_candle = True
            for result in batch.trend_results:
                emit_payload(_result_payload(result))
    finally:
        await stream.aclose()
    return 0


def main() -> int:
    return asyncio.run(run_trend_monitoring_worker())


if __name__ == "__main__":
    raise SystemExit(main())
