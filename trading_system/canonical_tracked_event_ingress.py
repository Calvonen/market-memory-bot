from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from trading_system.tracked_event_repository import TrackedEventTimeStatus


@dataclass(frozen=True)
class CanonicalTrackedEventWriteResult:
    event_id: str
    tracked_instrument_id: str
    event_date: date
    action: str


class SupabaseCanonicalTrackedEventIngress:
    """Producer-neutral Python boundary for canonical tracked-event creation."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseCanonicalTrackedEventIngress":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def register(
        self,
        *,
        company_name: str,
        instrument: str,
        market: str,
        source: str,
        external_key: str,
        kind: str,
        title: str,
        event_at: datetime,
        event_date: date,
        event_time_status: TrackedEventTimeStatus,
        actor: str,
        calendar_event_id: str | None = None,
    ) -> CanonicalTrackedEventWriteResult:
        if event_at.tzinfo is None or event_at.utcoffset() is None:
            raise ValueError("event_at must be timezone-aware")
        if isinstance(event_date, datetime) or not isinstance(event_date, date):
            raise ValueError("event_date must be a date")
        if not isinstance(event_time_status, TrackedEventTimeStatus):
            raise ValueError("event_time_status must be a TrackedEventTimeStatus")

        response = self.client.rpc(
            "upsert_canonical_tracked_market_event",
            {
                "input_company_name": company_name,
                "input_instrument": instrument,
                "input_market": market,
                "input_source": source,
                "input_external_key": external_key,
                "input_kind": kind,
                "input_title": title,
                "input_event_at": event_at.astimezone(UTC).isoformat(),
                "input_event_date": event_date.isoformat(),
                "input_event_time_status": event_time_status.value,
                "input_actor": actor,
                "input_calendar_event_id": calendar_event_id,
            },
        ).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("upsert_canonical_tracked_market_event returned no rows")
        row = rows[0]

        persisted_date = date.fromisoformat(str(row["out_event_date"]))
        if persisted_date != event_date:
            raise RuntimeError("canonical tracked event returned a different event_date")

        return CanonicalTrackedEventWriteResult(
            event_id=str(row["out_id"]),
            tracked_instrument_id=str(row["out_tracked_instrument_id"]),
            event_date=persisted_date,
            action=str(row["out_action"]),
        )
