from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any


class TrackedEventTimeStatus(str, Enum):
    CONFIRMED = "confirmed"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class TrackedEventStatus(str, Enum):
    TRACKED = "tracked"
    MONITORING = "monitoring"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# Active/history split for GET /api/v1/tracked-events (view=active|history):
# tracked/monitoring are always active; a terminal status stays active for
# this long past its terminal updated_at before it becomes history.
_ACTIVE_HISTORY_WINDOW = timedelta(hours=24)
_ALWAYS_ACTIVE_STATUSES = (
    TrackedEventStatus.TRACKED.value,
    TrackedEventStatus.MONITORING.value,
)
_TERMINAL_STATUSES = (
    TrackedEventStatus.COMPLETED.value,
    TrackedEventStatus.FAILED.value,
    TrackedEventStatus.CANCELLED.value,
)


@dataclass(frozen=True)
class PersistentTrackedEvent:
    event_id: str
    tracked_instrument_id: str
    calendar_event_id: str | None
    company_name: str
    instrument: str
    market: str
    source: str
    external_key: str
    kind: str
    title: str
    event_at: datetime
    event_time_status: TrackedEventTimeStatus
    status: TrackedEventStatus
    resolved_etoro_instrument_id: int | None = None
    resolved_etoro_symbol: str | None = None
    resolved_etoro_display_name: str | None = None
    resolved_etoro_market: str | None = None
    resolution_armed_at: datetime | None = None
    resolution_armed_by: str | None = None
    reference_price: Decimal | None = None
    reference_captured_at: datetime | None = None
    reference_kind: str | None = None
    reaction_anchor_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None
    created_by: str = ""
    updated_by: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Immutable snapshot of the effective reaction-monitoring settings this
    # event was actually tracked with (see tracked_event_config.py). None for
    # rows captured before this column existed - never fabricated here from
    # today's defaults, since that would silently misrepresent history.
    tracking_config_snapshot: dict[str, Any] | None = None
    # Persisted immutable pre-event context snapshot (see
    # pre_event_market_context_persistence.py). None until captured; the
    # worker uses this to skip redundant calendar/Yahoo reacquisition on
    # restart rather than issuing a separate lookup.
    pre_event_market_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class TrackedEventReactionRecord:
    tracked_market_event_id: str
    interval_minutes: int
    candle_start: datetime
    reference_price: Decimal
    close_price: Decimal
    return_pct: Decimal
    direction: str
    evolution: str
    observed_at: datetime


class SupabaseTrackedEventRepository:
    """Persistence boundary for generic tracked-event reaction monitoring."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseTrackedEventRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def upsert(
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
        event_time_status: TrackedEventTimeStatus,
        actor: str,
        calendar_event_id: str | None = None,
    ) -> tuple[PersistentTrackedEvent, str]:
        if event_at.tzinfo is None or event_at.utcoffset() is None:
            raise ValueError("event_at must be timezone-aware")
        response = self.client.rpc(
            "upsert_tracked_market_event",
            {
                "input_company_name": company_name,
                "input_instrument": instrument,
                "input_market": market,
                "input_source": source,
                "input_external_key": external_key,
                "input_kind": kind,
                "input_title": title,
                "input_event_at": event_at.astimezone(UTC).isoformat(),
                "input_event_time_status": event_time_status.value,
                "input_actor": actor,
                "input_calendar_event_id": calendar_event_id,
            },
        ).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("upsert_tracked_market_event returned no rows")
        row = rows[0]
        return self._row_to_event(row, out_prefix=True), str(row["out_action"])

    def get(self, event_id: str) -> PersistentTrackedEvent | None:
        response = (
            self.client.table("tracked_market_events")
            .select("*")
            .eq("id", event_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return self._row_to_event(rows[0]) if rows else None

    def list_recent(self, *, limit: int = 20) -> tuple[PersistentTrackedEvent, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        try:
            response = (
                self.client.table("tracked_market_events")
                .select("*")
                .order("event_at", desc=True)
                .limit(limit)
                .execute()
            )
            return tuple(self._row_to_event(row) for row in (response.data or []))
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"failed to list tracked market events: {exc}") from exc

    def list_active(
        self, *, limit: int = 20, now: datetime
    ) -> tuple[PersistentTrackedEvent, ...]:
        # tracked/monitoring are always active; a terminal event (completed/
        # failed/cancelled) stays active until 24h past its terminal
        # updated_at, then falls into list_history() - see mark_completed/
        # mark_failed above, which both bump updated_at on the terminal
        # transition. completed_at is not shared by failed/cancelled, so
        # updated_at is the only timestamp common to all three.
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        cutoff = (now - _ACTIVE_HISTORY_WINDOW).astimezone(UTC).isoformat()
        try:
            always_active_response = (
                self.client.table("tracked_market_events")
                .select("*")
                .in_("status", list(_ALWAYS_ACTIVE_STATUSES))
                .order("event_at", desc=True)
                .limit(limit)
                .execute()
            )
            recent_terminal_response = (
                self.client.table("tracked_market_events")
                .select("*")
                .in_("status", list(_TERMINAL_STATUSES))
                .gte("updated_at", cutoff)
                # updated_at is only the 24h cutoff filter above, never the
                # ranking: preselection must order by the same key (event_at)
                # as the merged result below, or a terminal row with an
                # older updated_at but a newer event_at than `limit` other
                # rows could be dropped here before it ever reaches the
                # final event_at top-N.
                .order("event_at", desc=True)
                .limit(limit)
                .execute()
            )
            rows = list(always_active_response.data or []) + list(
                recent_terminal_response.data or []
            )
            events_by_id: dict[str, PersistentTrackedEvent] = {}
            for row in rows:
                event = self._row_to_event(row)
                events_by_id[event.event_id] = event
            ordered = sorted(
                events_by_id.values(),
                key=lambda event: (event.event_at, event.event_id),
                reverse=True,
            )
            return tuple(ordered[:limit])
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"failed to list tracked market events: {exc}") from exc

    def list_history(
        self, *, limit: int = 20, now: datetime
    ) -> tuple[PersistentTrackedEvent, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        cutoff = (now - _ACTIVE_HISTORY_WINDOW).astimezone(UTC).isoformat()
        try:
            response = (
                self.client.table("tracked_market_events")
                .select("*")
                .in_("status", list(_TERMINAL_STATUSES))
                .lt("updated_at", cutoff)
                .order("updated_at", desc=True)
                .limit(limit)
                .execute()
            )
            return tuple(self._row_to_event(row) for row in (response.data or []))
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"failed to list tracked market events: {exc}") from exc

    def list_runnable(
        self,
        *,
        now: datetime,
        lookahead: timedelta,
        max_past: timedelta,
    ) -> tuple[PersistentTrackedEvent, ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        lower = (now - max_past).astimezone(UTC).isoformat()
        upper = (now + lookahead).astimezone(UTC).isoformat()
        response = (
            self.client.table("tracked_market_events")
            .select("*")
            .in_("status", [TrackedEventStatus.TRACKED.value, TrackedEventStatus.MONITORING.value])
            .gte("event_at", lower)
            .lte("event_at", upper)
            .order("event_at")
            .execute()
        )
        return tuple(self._row_to_event(row) for row in (response.data or []))

    def arm_resolution(
        self,
        *,
        event_id: str,
        etoro_instrument_id: int,
        etoro_symbol: str,
        etoro_display_name: str,
        actor: str,
    ) -> PersistentTrackedEvent:
        response = self.client.rpc(
            "arm_tracked_market_event_resolution",
            {
                "input_event_id": event_id,
                "input_etoro_instrument_id": etoro_instrument_id,
                "input_etoro_symbol": etoro_symbol,
                "input_etoro_display_name": etoro_display_name,
                "input_actor": actor,
            },
        ).execute()
        return self._single_event_response(
            response.data,
            error_message="arm_tracked_market_event_resolution returned invalid data",
        )

    def capture_reference(
        self,
        *,
        event_id: str,
        reference_price: Decimal,
        captured_at: datetime,
        reference_kind: str,
        etoro_instrument_id: int,
        etoro_symbol: str,
        etoro_display_name: str,
        actor: str,
    ) -> PersistentTrackedEvent:
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        response = self.client.rpc(
            "capture_tracked_market_event_reference",
            {
                "input_event_id": event_id,
                "input_reference_price": str(reference_price),
                "input_reference_captured_at": captured_at.astimezone(UTC).isoformat(),
                "input_reference_kind": reference_kind,
                "input_etoro_instrument_id": etoro_instrument_id,
                "input_etoro_symbol": etoro_symbol,
                "input_etoro_display_name": etoro_display_name,
                "input_actor": actor,
            },
        ).execute()
        return self._single_event_response(
            response.data,
            error_message="capture_tracked_market_event_reference returned invalid data",
        )

    def capture_resolved_etoro_market(
        self,
        *,
        event_id: str,
        etoro_instrument_id: int,
        etoro_symbol: str,
        etoro_display_name: str,
        etoro_market: str,
        actor: str,
    ) -> PersistentTrackedEvent:
        try:
            response = self.client.rpc(
                "capture_tracked_market_event_resolved_market",
                {
                    "input_event_id": event_id,
                    "input_etoro_instrument_id": etoro_instrument_id,
                    "input_etoro_symbol": etoro_symbol,
                    "input_etoro_display_name": etoro_display_name,
                    "input_etoro_market": etoro_market,
                    "input_actor": actor,
                },
            ).execute()
        except Exception as exc:
            if "tracked_market_event_resolved_market_conflict" in str(exc):
                raise RuntimeError(
                    f"tracked event {event_id} already has a different resolved_etoro_market"
                ) from exc
            raise
        return self._single_event_response(
            response.data,
            error_message="capture_tracked_market_event_resolved_market returned invalid data",
        )

    def capture_reaction_anchor(
        self,
        *,
        event_id: str,
        reaction_anchor_at: datetime,
        actor: str,
    ) -> PersistentTrackedEvent:
        if reaction_anchor_at.tzinfo is None or reaction_anchor_at.utcoffset() is None:
            raise ValueError("reaction_anchor_at must be timezone-aware")
        response = self.client.rpc(
            "capture_tracked_market_event_reaction_anchor",
            {
                "input_event_id": event_id,
                "input_reaction_anchor_at": reaction_anchor_at.astimezone(UTC).isoformat(),
                "input_actor": actor,
            },
        ).execute()
        return self._single_event_response(
            response.data,
            error_message="capture_tracked_market_event_reaction_anchor returned invalid data",
        )

    def capture_tracking_config_snapshot(
        self,
        *,
        event_id: str,
        snapshot: dict[str, Any],
        actor: str,
    ) -> PersistentTrackedEvent:
        try:
            response = self.client.rpc(
                "capture_tracked_market_event_config_snapshot",
                {
                    "input_event_id": event_id,
                    "input_tracking_config_snapshot": snapshot,
                    "input_actor": actor,
                },
            ).execute()
        except Exception as exc:
            # capture_tracked_market_event_config_snapshot() raises this exact
            # message (no dedicated errcode, same as the sibling
            # capture_tracked_market_event_reference()'s _locked conflict) when
            # a *different* snapshot is already stored - never overwritten.
            # Translate it to RuntimeError so callers can distinguish this
            # permanent, non-retryable conflict from a transient RPC failure.
            if "tracked_market_event_config_snapshot_locked" in str(exc):
                raise RuntimeError(
                    f"tracked event {event_id} already has a different tracking_config_snapshot"
                ) from exc
            raise
        return self._single_event_response(
            response.data,
            error_message="capture_tracked_market_event_config_snapshot returned invalid data",
        )

    def mark_monitoring(self, event_id: str, *, actor: str, started_at: datetime) -> None:
        (
            self.client.table("tracked_market_events")
            .update(
                {
                    "status": TrackedEventStatus.MONITORING.value,
                    "started_at": started_at.astimezone(UTC).isoformat(),
                    "updated_by": actor,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "last_error": None,
                }
            )
            .eq("id", event_id)
            .in_("status", [TrackedEventStatus.TRACKED.value, TrackedEventStatus.MONITORING.value])
            .execute()
        )

    def mark_completed(self, event_id: str, *, actor: str, completed_at: datetime) -> None:
        (
            self.client.table("tracked_market_events")
            .update(
                {
                    "status": TrackedEventStatus.COMPLETED.value,
                    "completed_at": completed_at.astimezone(UTC).isoformat(),
                    "updated_by": actor,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "last_error": None,
                }
            )
            .eq("id", event_id)
            .in_("status", [TrackedEventStatus.TRACKED.value, TrackedEventStatus.MONITORING.value])
            .execute()
        )

    def mark_failed(self, event_id: str, *, actor: str, error: str) -> None:
        (
            self.client.table("tracked_market_events")
            .update(
                {
                    "status": TrackedEventStatus.FAILED.value,
                    "last_error": error[:1000],
                    "updated_by": actor,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("id", event_id)
            .in_("status", [TrackedEventStatus.TRACKED.value, TrackedEventStatus.MONITORING.value])
            .execute()
        )

    def save_reaction(self, record: TrackedEventReactionRecord) -> None:
        payload = {
            "tracked_market_event_id": record.tracked_market_event_id,
            "interval_minutes": record.interval_minutes,
            "candle_start": record.candle_start.astimezone(UTC).isoformat(),
            "reference_price": str(record.reference_price),
            "close_price": str(record.close_price),
            "return_pct": str(record.return_pct),
            "direction": record.direction,
            "evolution": record.evolution,
            "observed_at": record.observed_at.astimezone(UTC).isoformat(),
        }
        (
            self.client.table("tracked_market_event_reactions")
            .upsert(
                payload,
                on_conflict="tracked_market_event_id,interval_minutes,candle_start",
            )
            .execute()
        )

    def list_reactions(self, event_id: str) -> tuple[TrackedEventReactionRecord, ...]:
        response = (
            self.client.table("tracked_market_event_reactions")
            .select("*")
            .eq("tracked_market_event_id", event_id)
            .order("candle_start")
            .order("interval_minutes")
            .execute()
        )
        return tuple(self._row_to_reaction(row) for row in (response.data or []))

    @classmethod
    def _single_event_response(cls, data: Any, *, error_message: str) -> PersistentTrackedEvent:
        if isinstance(data, list):
            if not data:
                raise RuntimeError(error_message)
            row = data[0]
        elif isinstance(data, dict):
            row = data
        else:
            raise RuntimeError(error_message)
        return cls._row_to_event(row)

    @classmethod
    def _row_to_event(cls, row: dict[str, Any], *, out_prefix: bool = False) -> PersistentTrackedEvent:
        prefix = "out_" if out_prefix else ""

        def value(name: str, default: Any = None) -> Any:
            return row.get(f"{prefix}{name}", default)

        return PersistentTrackedEvent(
            event_id=str(value("id")),
            tracked_instrument_id=str(value("tracked_instrument_id")),
            calendar_event_id=str(value("calendar_event_id")) if value("calendar_event_id") else None,
            company_name=str(value("company_name") or ""),
            instrument=str(value("instrument")),
            market=str(value("market")),
            source=str(value("source")),
            external_key=str(value("external_key")),
            kind=str(value("kind")),
            title=str(value("title") or ""),
            event_at=cls._parse_datetime(value("event_at")),
            event_time_status=TrackedEventTimeStatus(value("event_time_status")),
            status=TrackedEventStatus(value("status")),
            resolved_etoro_instrument_id=(
                int(value("resolved_etoro_instrument_id"))
                if value("resolved_etoro_instrument_id") is not None
                else None
            ),
            resolved_etoro_symbol=(str(value("resolved_etoro_symbol")) if value("resolved_etoro_symbol") else None),
            resolved_etoro_display_name=(
                str(value("resolved_etoro_display_name"))
                if value("resolved_etoro_display_name")
                else None
            ),
            resolved_etoro_market=(
                str(value("resolved_etoro_market")) if value("resolved_etoro_market") else None
            ),
            resolution_armed_at=cls._parse_datetime_optional(value("resolution_armed_at")),
            resolution_armed_by=(str(value("resolution_armed_by")) if value("resolution_armed_by") else None),
            reference_price=(Decimal(str(value("reference_price"))) if value("reference_price") is not None else None),
            reference_captured_at=cls._parse_datetime_optional(value("reference_captured_at")),
            reference_kind=(str(value("reference_kind")) if value("reference_kind") else None),
            reaction_anchor_at=cls._parse_datetime_optional(value("reaction_anchor_at")),
            started_at=cls._parse_datetime_optional(value("started_at")),
            completed_at=cls._parse_datetime_optional(value("completed_at")),
            last_error=(str(value("last_error")) if value("last_error") else None),
            created_by=str(value("created_by") or ""),
            updated_by=str(value("updated_by") or ""),
            created_at=cls._parse_datetime_optional(value("created_at")),
            updated_at=cls._parse_datetime_optional(value("updated_at")),
            # NULL stays None here - a missing/NULL snapshot must never be
            # defaulted to {} or invented from current settings, or history
            # would silently look like it was tracked with today's config.
            tracking_config_snapshot=value("tracking_config_snapshot"),
            # NULL stays None here for the same reason as tracking_config_snapshot
            # above - a not-yet-captured context must never be defaulted to {}.
            pre_event_market_context=value("pre_event_market_context"),
        )

    @classmethod
    def _row_to_reaction(cls, row: dict[str, Any]) -> TrackedEventReactionRecord:
        return TrackedEventReactionRecord(
            tracked_market_event_id=str(row["tracked_market_event_id"]),
            interval_minutes=int(row["interval_minutes"]),
            candle_start=cls._parse_datetime(row["candle_start"]),
            reference_price=Decimal(str(row["reference_price"])),
            close_price=Decimal(str(row["close_price"])),
            return_pct=Decimal(str(row["return_pct"])),
            direction=str(row["direction"]),
            evolution=str(row["evolution"]),
            observed_at=cls._parse_datetime(row["observed_at"]),
        )

    @staticmethod
    def _parse_datetime(value: str | datetime | None) -> datetime:
        parsed = SupabaseTrackedEventRepository._parse_datetime_optional(value)
        if parsed is None:
            raise RuntimeError("tracked event timestamp is missing")
        return parsed

    @staticmethod
    def _parse_datetime_optional(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))