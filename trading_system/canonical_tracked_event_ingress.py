from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from trading_system.tracked_event_repository import TrackedEventTimeStatus
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


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

    def register_for_tracked_instrument(
        self,
        tracked: TrackedEtoroInstrument,
        *,
        company_name: str,
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
        result = self.register(
            company_name=company_name,
            instrument=tracked.instrument,
            market=tracked.market,
            source=source,
            external_key=external_key,
            kind=kind,
            title=title,
            event_at=event_at,
            event_date=event_date,
            event_time_status=event_time_status,
            actor=actor,
            calendar_event_id=calendar_event_id,
            expected_tracked_instrument_id=tracked.tracked_instrument_id,
        )
        if result.tracked_instrument_id != tracked.tracked_instrument_id:
            raise RuntimeError(
                "canonical tracked event resolved to a different tracked instrument"
            )
        return result

    def register_assistant_proposal_for_tracked_instrument(
        self,
        tracked: TrackedEtoroInstrument,
        *,
        proposal_id: str,
        expected_base_version: int,
        company_name: str,
        source: str,
        external_key: str,
        kind: str,
        title: str,
        event_at: datetime,
        event_date: date,
        event_time_status: TrackedEventTimeStatus,
        actor: str,
    ) -> CanonicalTrackedEventWriteResult:
        """CAS existing event metadata against the proposal's reviewed version."""
        self._validate_inputs(
            event_at=event_at,
            event_date=event_date,
            event_time_status=event_time_status,
            expected_tracked_instrument_id=tracked.tracked_instrument_id,
            calendar_event_id=None,
        )
        if expected_base_version < 1:
            raise ValueError("expected_base_version must be positive")

        response = self.client.rpc(
            "upsert_assistant_proposal_canonical_tracked_event",
            {
                "input_proposal_id": proposal_id,
                "input_expected_base_version": expected_base_version,
                "input_company_name": company_name,
                "input_instrument": tracked.instrument,
                "input_market": tracked.market,
                "input_source": source,
                "input_external_key": external_key,
                "input_kind": kind,
                "input_title": title,
                "input_event_at": event_at.astimezone(UTC).isoformat(),
                "input_event_date": event_date.isoformat(),
                "input_event_time_status": event_time_status.value,
                "input_actor": actor,
                "input_expected_tracked_instrument_id": tracked.tracked_instrument_id,
            },
        ).execute()
        result = self._parse_result(
            response.data,
            event_date=event_date,
            expected_tracked_instrument_id=tracked.tracked_instrument_id,
            rpc_name="upsert_assistant_proposal_canonical_tracked_event",
        )
        if result.tracked_instrument_id != tracked.tracked_instrument_id:
            raise RuntimeError(
                "canonical tracked event resolved to a different tracked instrument"
            )
        return result

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
        expected_tracked_instrument_id: str | None = None,
    ) -> CanonicalTrackedEventWriteResult:
        self._validate_inputs(
            event_at=event_at,
            event_date=event_date,
            event_time_status=event_time_status,
            expected_tracked_instrument_id=expected_tracked_instrument_id,
            calendar_event_id=calendar_event_id,
        )

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
                "input_calendar_event_id": None,
                "input_expected_tracked_instrument_id": expected_tracked_instrument_id,
            },
        ).execute()
        return self._parse_result(
            response.data,
            event_date=event_date,
            expected_tracked_instrument_id=expected_tracked_instrument_id,
            rpc_name="upsert_canonical_tracked_market_event",
        )

    @staticmethod
    def _validate_inputs(
        *,
        event_at: datetime,
        event_date: date,
        event_time_status: TrackedEventTimeStatus,
        expected_tracked_instrument_id: str | None,
        calendar_event_id: str | None,
    ) -> None:
        if event_at.tzinfo is None or event_at.utcoffset() is None:
            raise ValueError("event_at must be timezone-aware")
        if isinstance(event_date, datetime) or not isinstance(event_date, date):
            raise ValueError("event_date must be a date")
        if not isinstance(event_time_status, TrackedEventTimeStatus):
            raise ValueError("event_time_status must be a TrackedEventTimeStatus")
        if expected_tracked_instrument_id is not None and not expected_tracked_instrument_id.strip():
            raise ValueError("expected_tracked_instrument_id must not be blank")
        if calendar_event_id is not None:
            raise ValueError(
                "calendar_event_id is not accepted by canonical tracked-event ingress; "
                "use calendar runtime promotion"
            )

    @staticmethod
    def _parse_result(
        data: Any,
        *,
        event_date: date,
        expected_tracked_instrument_id: str | None,
        rpc_name: str,
    ) -> CanonicalTrackedEventWriteResult:
        rows = data or []
        if not rows:
            raise RuntimeError(f"{rpc_name} returned no rows")
        row = rows[0]
        persisted_date = date.fromisoformat(str(row["out_event_date"]))
        if persisted_date != event_date:
            raise RuntimeError("canonical tracked event returned a different event_date")
        persisted_tracked_instrument_id = str(row["out_tracked_instrument_id"])
        if (
            expected_tracked_instrument_id is not None
            and persisted_tracked_instrument_id != expected_tracked_instrument_id
        ):
            raise RuntimeError(
                "canonical tracked event returned a different tracked instrument"
            )
        return CanonicalTrackedEventWriteResult(
            event_id=str(row["out_id"]),
            tracked_instrument_id=persisted_tracked_instrument_id,
            event_date=persisted_date,
            action=str(row["out_action"]),
        )
