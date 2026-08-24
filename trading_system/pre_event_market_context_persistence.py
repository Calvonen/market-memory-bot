from __future__ import annotations

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
) -> PersistentTrackedEvent:
    """Persist one validated immutable pre-event market-context snapshot.

    Session/calendar resolution stays outside this adapter. The caller must pass
    the already-grounded market timezone and the exact serialized snapshot; the
    database RPC remains the authority for schema, event-date, lifecycle, and
    immutability validation.
    """
    try:
        response = repository.client.rpc(
            "capture_tracked_market_event_pre_event_context",
            {
                "input_event_id": event_id,
                "input_pre_event_market_context": snapshot,
                "input_market_timezone": market_timezone,
                "input_actor": actor,
            },
        ).execute()
    except Exception as exc:
        if "tracked_market_event_pre_event_context_locked" in str(exc):
            raise RuntimeError(
                f"tracked event {event_id} already has a different pre_event_market_context"
            ) from exc
        raise

    return repository._single_event_response(
        response.data,
        error_message="capture_tracked_market_event_pre_event_context returned invalid data",
    )
