from __future__ import annotations

from datetime import UTC, datetime

from trading_system.tracked_event_repository import (
    SupabaseTrackedEventRepository,
    TrackedEventStatus,
)


def fail_tracked_event_if_current(
    repository: SupabaseTrackedEventRepository,
    *,
    event_id: str,
    expected_event_updated_at: datetime,
    actor: str,
    error: str,
) -> None:
    """Fail an unreferenced TRACKED row only if its exact version is unchanged.

    The filtered UPDATE is one database statement, so a concurrent reference
    capture, MONITORING transition, reschedule, or any other version-changing
    write makes the predicate miss instead of terminal-failing a row that has
    already progressed.

    Lightweight repository fakes used by worker unit tests do not expose the
    Supabase client; for those interface-only fakes we preserve the historical
    ``mark_failed`` behavior. Production ``SupabaseTrackedEventRepository``
    instances always expose ``client`` and therefore always take the CAS path.
    """
    if expected_event_updated_at.tzinfo is None or expected_event_updated_at.utcoffset() is None:
        raise ValueError("expected_event_updated_at must be timezone-aware")
    if not event_id.strip():
        raise ValueError("event_id is required")
    if not actor.strip():
        raise ValueError("actor is required")

    client = getattr(repository, "client", None)
    if client is None:
        repository.mark_failed(event_id, actor=actor, error=error)
        return

    response = (
        client.table("tracked_market_events")
        .update(
            {
                "status": TrackedEventStatus.FAILED.value,
                "last_error": error[:1000],
                "updated_by": actor,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        .eq("id", event_id)
        .eq("updated_at", expected_event_updated_at.astimezone(UTC).isoformat())
        .eq("status", TrackedEventStatus.TRACKED.value)
        .is_("reference_price", "null")
        .execute()
    )
    rows = response.data or []
    if len(rows) != 1:
        raise RuntimeError(
            "tracked event changed before the version-bound failure could be recorded"
        )
