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
    session_close: datetime | None = None,
) -> PersistentTrackedEvent:
    if session_close is not None and expected_event_updated_at is None:
        raise ValueError("session_close requires expected_event_updated_at")
    if session_close is not None and (
        session_close.tzinfo is None or session_close.utcoffset() is None
    ):
        raise ValueError("session_close must be timezone-aware")

    if expected_event_updated_at is not None:
        if (
            expected_event_updated_at.tzinfo is None
            or expected_event_updated_at.utcoffset() is None
        ):
            raise ValueError("expected_event_updated_at must be timezone-aware")
        payload = {
            "input_event_id": event_id,
            "input_pre_event_market_context": snapshot,
            "input_market_timezone": market_timezone,
            "input_actor": actor,
            "input_expected_updated_at": expected_event_updated_at.astimezone(UTC).isoformat(),
        }
        if session_close is not None:
            rpc_name = "capture_tracked_market_event_pre_event_context_validated"
            payload["input_session_close"] = session_close.astimezone(UTC).isoformat()
        else:
            rpc_name = "capture_tracked_market_event_pre_event_context_if_current"
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
        message = str(exc)
        if "tracked_market_event_pre_event_context_locked" in message:
            raise RuntimeError(
                f"tracked event {event_id} already has a different pre_event_market_context"
            ) from exc
        if "tracked_market_event_version_conflict" in message:
            raise RuntimeError(
                f"tracked event {event_id} changed before pre-event context capture"
            ) from exc
        if "pre_event_market_context_session_not_closed_yet" in message:
            raise RuntimeError(
                f"tracked event {event_id} pre-event session had not closed when capture ran"
            ) from exc
        if "pre_event_market_context_session_not_closed_before_event" in message:
            raise RuntimeError(
                f"tracked event {event_id} pre-event session does not close before event_at"
            ) from exc
        if "pre_event_market_context_session_close_mismatch" in message:
            raise RuntimeError(
                f"tracked event {event_id} session close does not belong to the snapshot session"
            ) from exc
        if "pre_event_market_context_not_before_event" in message:
            raise RuntimeError(
                f"tracked event {event_id} pre-event context is not before the event"
            ) from exc
        raise

    event = repository.get(event_id)
    if event is None:
        raise RuntimeError("captured pre-event market context event could not be re-read")
    return event


def validate_pre_event_market_context_if_current(
    repository: SupabaseTrackedEventRepository,
    *,
    event_id: str,
    expected_event_updated_at: datetime,
) -> PersistentTrackedEvent:
    if expected_event_updated_at.tzinfo is None or expected_event_updated_at.utcoffset() is None:
        raise ValueError("expected_event_updated_at must be timezone-aware")

    try:
        repository.client.rpc(
            "validate_tracked_market_event_pre_event_context_if_current",
            {
                "input_event_id": event_id,
                "input_expected_updated_at": expected_event_updated_at.astimezone(UTC).isoformat(),
            },
        ).execute()
    except Exception as exc:
        if "tracked_market_event_version_conflict" in str(exc):
            raise RuntimeError(
                f"tracked event {event_id} changed before pre-event context revalidation completed"
            ) from exc
        raise

    event = repository.get(event_id)
    if event is None:
        raise RuntimeError("revalidated tracked event could not be re-read")
    return event


def fail_pre_event_deadline_if_current(
    repository: SupabaseTrackedEventRepository,
    *,
    event_id: str,
    expected_event_updated_at: datetime,
    actor: str,
    error: str,
) -> None:
    if expected_event_updated_at.tzinfo is None or expected_event_updated_at.utcoffset() is None:
        raise ValueError("expected_event_updated_at must be timezone-aware")

    try:
        repository.client.rpc(
            "fail_tracked_market_event_pre_event_deadline_if_current",
            {
                "input_event_id": event_id,
                "input_expected_updated_at": expected_event_updated_at.astimezone(UTC).isoformat(),
                "input_actor": actor,
                "input_error": error,
            },
        ).execute()
    except Exception as exc:
        message = str(exc)
        if "tracked_market_event_version_conflict" in message:
            raise RuntimeError(
                f"tracked event {event_id} changed before its pre-event deadline failure could be recorded"
            ) from exc
        if "tracked_market_event_pre_event_deadline_not_reached" in message:
            raise RuntimeError(
                f"tracked event {event_id} was rescheduled past its pre-event deadline before the failure could be recorded"
            ) from exc
        if "tracked_market_event_not_pre_event_failable" in message:
            raise RuntimeError(
                f"tracked event {event_id} is no longer awaiting a pre-event baseline"
            ) from exc
        raise
