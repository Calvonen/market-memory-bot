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
    """Persist one validated immutable pre-event market-context snapshot.

    Session/calendar resolution stays outside this adapter. The caller must pass
    the already-grounded market timezone and the exact serialized snapshot; the
    database RPC remains the authority for schema, event-date, lifecycle, and
    immutability validation.

    When ``expected_event_updated_at`` is supplied, capture is compare-and-swap
    bound to that exact tracked-event version. A concurrent event update fails
    closed before the immutable context can be written.

    ``session_close`` is the exchange calendar's close for the snapshot's own
    session. Supplying it (together with the expected version) selects the
    canonical validated RPC, which is the only path allowed to persist a
    snapshot dated on the event's own market day - and only after the database
    has checked that close against the row's event_at and its own clock. Without
    it the base RPC is used, which accepts strictly earlier sessions only.
    """
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
        if "tracked_market_event_pre_event_context_locked" in str(exc):
            raise RuntimeError(
                f"tracked event {event_id} already has a different pre_event_market_context"
            ) from exc
        if "tracked_market_event_version_conflict" in str(exc):
            raise RuntimeError(
                f"tracked event {event_id} changed before pre-event context capture"
            ) from exc
        if "pre_event_market_context_session_not_closed_yet" in str(exc):
            raise RuntimeError(
                f"tracked event {event_id} pre-event session had not closed when capture ran"
            ) from exc
        if "pre_event_market_context_session_not_closed_before_event" in str(exc):
            raise RuntimeError(
                f"tracked event {event_id} pre-event session does not close before event_at"
            ) from exc
        if "pre_event_market_context_session_close_mismatch" in str(exc):
            raise RuntimeError(
                f"tracked event {event_id} session close does not belong to the snapshot session"
            ) from exc
        if "pre_event_market_context_not_before_event" in str(exc):
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
    """Atomically confirm the tracked event is still at the expected version.

    Calendar/session revalidation of an already-persisted pre_event_market_context
    snapshot happens in the worker against a row read moments earlier. Nothing
    stops event_at from being edited (see upsert_tracked_market_event) in the
    gap between that read and the decision to proceed to monitoring. This locks
    the current row and enforces an exact updated_at match in one transaction,
    so a concurrent edit during revalidation is guaranteed to be observed as a
    version conflict instead of letting monitoring start on a stale read.
    """
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
) -> bool:
    """Attempt a version-bound terminal pre-event deadline failure.

    Returns ``True`` only when the canonical RPC records FAILED. Returns
    ``False`` when the row changed, was rescheduled so the deadline is no longer
    reached, or already progressed/prepared. Those outcomes are expected CAS
    rejections, not worker-fatal errors: the next poll must re-read the current
    row and decide again. Unexpected RPC/transport failures still raise.
    """
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
        if (
            "tracked_market_event_version_conflict" in message
            or "tracked_market_event_pre_event_deadline_not_reached" in message
            or "tracked_market_event_not_pre_event_failable" in message
        ):
            return False
        raise

    return True
