from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    SupabaseTrackedEventRepository,
)


def capture_pre_event_market_context(
    repository: SupabaseTrackedEventRepository,
    *,
    event_id: str,
    snapshot: dict[str, Any],
    market_timezone: str,
    actor: str,
    expected_event_updated_at: datetime | None = None,
) -> PersistentTrackedEvent:
    """Persist one validated immutable pre-event market-context snapshot.

    Session/calendar resolution stays outside this adapter. The caller must pass
    the already-grounded market timezone and the exact serialized snapshot; the
    database RPC remains the authority for schema, event-date, lifecycle, and
    immutability validation.

    When ``expected_event_updated_at`` is supplied, capture is compare-and-swap
    bound to that exact tracked-event version. A concurrent event update fails
    closed before the immutable context can be written.
    """
    if expected_event_updated_at is not None:
        if (
            expected_event_updated_at.tzinfo is None
            or expected_event_updated_at.utcoffset() is None
        ):
            raise ValueError("expected_event_updated_at must be timezone-aware")
        rpc_name = "capture_tracked_market_event_pre_event_context_if_current"
        payload = {
            "input_event_id": event_id,
            "input_pre_event_market_context": snapshot,
            "input_market_timezone": market_timezone,
            "input_actor": actor,
            "input_expected_updated_at": expected_event_updated_at.astimezone(UTC).isoformat(),
        }
    else:
        rpc_name = "capture_tracked_market_event_pre_event_context"
        payload = {
            "input_event_id": event_id,
            "input_pre_event_market_context": snapshot,
            "input_market_timezone": market_timezone,
            "input_actor": actor,
        }

    try:
        repository.client.rpc(rpc_name, payload).execute()
    except Exception as exc:
        if "tracked_market_event_pre_event_context_locked" in str(exc):
            raise RuntimeError(
                f"tracked event {event_id} already has a different pre_event_market_context"
            ) from exc
        if "tracked_market_event_version_conflict" in str(exc):
            raise RuntimeError(
                f"tracked event {event_id} changed before pre-event context capture"
            ) from exc
        raise

    event = repository.get(event_id)
    if event is None:
        raise RuntimeError("captured pre-event market context event could not be re-read")
    return event
