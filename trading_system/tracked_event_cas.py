from __future__ import annotations

from datetime import datetime

from trading_system.tracked_event_repository import SupabaseTrackedEventRepository


def fail_tracked_event_if_current(
    repository: SupabaseTrackedEventRepository,
    *,
    event_id: str,
    expected_event_updated_at: datetime,
    actor: str,
    error: str,
) -> None:
    """Fail a proven-stale persisted context through the canonical CAS RPC.

    Production repositories always expose the Supabase client and therefore use
    the database RPC. Lightweight unit-test fakes may expose only mark_failed;
    that fallback exists solely to preserve their interface boundary.
    """
    if expected_event_updated_at.tzinfo is None or expected_event_updated_at.utcoffset() is None:
        raise ValueError("expected_event_updated_at must be timezone-aware")
    if not event_id.strip():
        raise ValueError("event_id is required")
    if not actor.strip():
        raise ValueError("actor is required")
    if not error.strip():
        raise ValueError("error is required")

    client = getattr(repository, "client", None)
    if client is None:
        repository.mark_failed(event_id, actor=actor, error=error)
        return

    try:
        client.rpc(
            "fail_tracked_market_event_stale_context_if_current",
            {
                "input_event_id": event_id,
                "input_expected_updated_at": expected_event_updated_at.isoformat(),
                "input_actor": actor,
                "input_error": error,
            },
        ).execute()
    except Exception as exc:
        message = str(exc)
        if (
            "tracked_market_event_version_conflict" in message
            or "tracked_market_event_not_stale_context_failable" in message
        ):
            raise RuntimeError(
                "tracked event changed before the version-bound stale-context failure could be recorded"
            ) from exc
        raise
