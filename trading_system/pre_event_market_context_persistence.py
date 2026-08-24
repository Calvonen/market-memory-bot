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
) -> None:
    """Terminal-fail an event whose pre-event deadline passed, version-bound.

    'failed' is terminal, so this decision must never be made from a stale copy
    of event_at: upsert_tracked_market_event can reschedule a TRACKED,
    reference-free event at any time, and a plain read-then-mark_failed would
    still terminal-fail an event whose deadline no longer applies.

    The RPC locks the row, requires this exact version, re-checks the deadline
    against the locked row's own event_at, and confirms the event is still
    awaiting its pre-event baseline before writing. Every way that can fail -
    a concurrent write, a reschedule into the future, or an event that already
    moved on - raises instead of failing the event, and is retryable: the next
    poll re-evaluates whatever the event now is.
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
        if "tracked_market_event_version_conflict" in message:
            raise RuntimeError(
                f"tracked event {event_id} changed before its pre-event deadline failure "
                "could be recorded"
            ) from exc
        if "tracked_market_event_pre_event_deadline_not_reached" in message:
            raise RuntimeError(
                f"tracked event {event_id} was rescheduled past its pre-event deadline "
                "before the failure could be recorded"
            ) from exc
        if "tracked_market_event_not_pre_event_failable" in message:
            raise RuntimeError(
                f"tracked event {event_id} is no longer awaiting a pre-event baseline"
            ) from exc
        raise
