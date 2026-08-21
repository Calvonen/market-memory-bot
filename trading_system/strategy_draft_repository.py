from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any, Protocol

from trading_system.event_repository import InMemoryEventExpectationRepository
from trading_system.models import utc_now


class ExpectationVersionConflict(Exception):
    """The current expectation version no longer matches the base version
    this approval was based on - either a genuine concurrent approval, or a
    retried/duplicated request replaying an approval that already
    succeeded. Either way, the caller must preview again before retrying."""


class StrategyDraftEventNotFound(Exception):
    """No expectation version exists yet for this event_id."""


@dataclass(frozen=True)
class StrategyDraftApprovalResult:
    version: int
    updated_at: datetime


class StrategyDraftApprovalRepository(Protocol):
    """Atomic CAS-and-persist for the strategy-draft approve endpoint.

    A single call either: verifies the current expectation version equals
    expected_base_version, inserts the next expectation version, and
    records the approval audit trail - all three, atomically - or none of
    them happen and ExpectationVersionConflict/StrategyDraftEventNotFound is
    raised. This is deliberately a different operation from
    EventExpectationRepository.save(): that method's own max(version)+1
    retry loop has no way to enforce "the version I previewed against is
    still current," so it must never be used to implement this CAS.
    """

    def approve(
        self,
        *,
        event_id: str,
        expected_base_version: int,
        source_name: str | None,
        source_url: str | None,
        source_as_of: date | None,
        consensus: dict[str, Any],
        important_kpis: list[str],
        bull_case: list[str],
        base_case: list[str],
        bear_case: list[str],
        triggers: dict[str, Any],
        invalidation_conditions: list[str],
        change_note: str,
        draft_fingerprint: str,
        approved_by: str,
        approved_via: str,
    ) -> StrategyDraftApprovalResult: ...


@dataclass
class InMemoryStrategyDraftApprovalRepository:
    """Test double mirroring the atomicity contract of the Postgres RPC
    (see supabase/migrations/20260821090000_event_strategy_approvals.sql):
    a lock serializes concurrent callers for the same event (equivalent to
    the RPC's pg_advisory_xact_lock), and the expectation dict is only
    mutated after every step - including the simulated audit insert -
    succeeds, so a failure partway through leaves nothing changed. Real
    atomicity/isolation is provided by Postgres in production; this class
    exists only so the approval contract can be unit-tested without one.

    Deliberately reuses `expectations.lock` rather than a lock of its own:
    in production, approve_strategy_draft() and the admin write endpoint's
    insert_next_expectation_version() both take the *same*
    pg_advisory_xact_lock for a given event_id, so a concurrent admin write
    and approval can never race each other at the database level. Sharing
    one lock object here is what makes that same guarantee testable against
    the in-memory doubles.
    """

    expectations: InMemoryEventExpectationRepository
    audit_records: list[dict[str, Any]] = field(default_factory=list)
    # Test-only fault injection: simulate the audit insert failing inside
    # the same transaction as the expectation-version insert, to prove nothing
    # is left half-written.
    fail_audit_insert: bool = False

    def approve(
        self,
        *,
        event_id: str,
        expected_base_version: int,
        source_name: str | None,
        source_url: str | None,
        source_as_of: date | None,
        consensus: dict[str, Any],
        important_kpis: list[str],
        bull_case: list[str],
        base_case: list[str],
        bear_case: list[str],
        triggers: dict[str, Any],
        invalidation_conditions: list[str],
        change_note: str,
        draft_fingerprint: str,
        approved_by: str,
        approved_via: str,
    ) -> StrategyDraftApprovalResult:
        with self.expectations.lock:
            current = self.expectations.events.get(event_id)
            if current is None:
                raise StrategyDraftEventNotFound(event_id)
            if current.version != expected_base_version:
                raise ExpectationVersionConflict(
                    f"expected {expected_base_version} but current is {current.version}"
                )

            next_version = expected_base_version + 1
            updated_at = utc_now()
            candidate = replace(
                current,
                source_name=source_name,
                source_url=source_url,
                source_as_of=source_as_of,
                consensus=consensus,
                important_kpis=tuple(important_kpis),
                bull_case=tuple(bull_case),
                base_case=tuple(base_case),
                bear_case=tuple(bear_case),
                triggers=triggers,
                invalidation_conditions=tuple(invalidation_conditions),
                version=next_version,
                updated_at=updated_at,
            )

            if self.fail_audit_insert:
                # Simulates the audit insert failing inside the same DB
                # transaction as the expectation-version insert: raised
                # before either in-memory structure below is touched, so
                # the caller sees no version bump and no audit row - same
                # observable outcome as a rolled-back Postgres transaction.
                raise RuntimeError("simulated audit insert failure")

            self.expectations.events[event_id] = candidate
            self.audit_records.append(
                {
                    "event_id": event_id,
                    "expectation_version": next_version,
                    "base_expectation_version": expected_base_version,
                    "draft_fingerprint": draft_fingerprint,
                    "approved_by": approved_by,
                    "approved_via": approved_via,
                    "change_note": change_note,
                    "created_at": updated_at,
                }
            )
            return StrategyDraftApprovalResult(version=next_version, updated_at=updated_at)


class SupabaseStrategyDraftApprovalRepository:
    """Calls the approve_strategy_draft() Postgres RPC - see
    supabase/migrations/20260821090000_event_strategy_approvals.sql for the
    atomic CAS-check + expectation-version insert + audit insert this
    performs as a single transaction."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseStrategyDraftApprovalRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def approve(
        self,
        *,
        event_id: str,
        expected_base_version: int,
        source_name: str | None,
        source_url: str | None,
        source_as_of: date | None,
        consensus: dict[str, Any],
        important_kpis: list[str],
        bull_case: list[str],
        base_case: list[str],
        bear_case: list[str],
        triggers: dict[str, Any],
        invalidation_conditions: list[str],
        change_note: str,
        draft_fingerprint: str,
        approved_by: str,
        approved_via: str,
    ) -> StrategyDraftApprovalResult:
        params = {
            "input_event_id": event_id,
            "input_expected_base_version": expected_base_version,
            "input_source_name": source_name,
            "input_source_url": source_url,
            "input_source_as_of": source_as_of.isoformat() if source_as_of else None,
            "input_consensus": consensus,
            "input_important_kpis": list(important_kpis),
            "input_bull_case": list(bull_case),
            "input_base_case": list(base_case),
            "input_bear_case": list(bear_case),
            "input_triggers": triggers,
            "input_invalidation_conditions": list(invalidation_conditions),
            "input_change_note": change_note,
            "input_draft_fingerprint": draft_fingerprint,
            "input_approved_by": approved_by,
            "input_approved_via": approved_via,
        }
        try:
            response = self.client.rpc("approve_strategy_draft", params).execute()
        except Exception as exc:
            if self._is_version_conflict(exc):
                raise ExpectationVersionConflict(str(exc)) from exc
            if self._is_event_not_found(exc):
                raise StrategyDraftEventNotFound(event_id) from exc
            raise

        rows = response.data or []
        if not rows:
            raise RuntimeError("approve_strategy_draft returned no rows")
        row = rows[0]
        return StrategyDraftApprovalResult(
            version=int(row["new_version"]),
            updated_at=self._parse_datetime(row.get("created_at")) or utc_now(),
        )

    @staticmethod
    def _is_version_conflict(exc: Exception) -> bool:
        # The custom application-level conflict, and a defense-in-depth
        # fallback: shared pg_advisory_xact_lock use across every
        # event_expectation_versions writer (see
        # supabase/migrations/*_shared_expectation_version_lock.sql) should
        # make a raw unique-constraint violation (23505) here unreachable in
        # practice, but if it were ever hit - e.g. a writer added later that
        # forgets to take the lock - it must still surface as a controlled
        # 409, never an unmapped 500.
        message = str(exc)
        code = getattr(exc, "code", None)
        return (
            "expectation_version_conflict" in message
            or code == "P0001"
            or code == "23505"
            or "23505" in message
        )

    @staticmethod
    def _is_event_not_found(exc: Exception) -> bool:
        return "event_not_found" in str(exc) or getattr(exc, "code", None) == "P0002"

    @staticmethod
    def _parse_datetime(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
