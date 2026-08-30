from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from trading_system.models import TradingMode


class TradingTaskState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CanonicalTradingTaskExecutionContext:
    """Read-only execution authority projected from an approved canonical task."""

    task_id: str
    source_event_id: str
    instrument: str
    mode: TradingMode

    def __post_init__(self) -> None:
        task_id = self.task_id.strip()
        source_event_id = self.source_event_id.strip()
        instrument = self.instrument.strip().upper()
        if not task_id:
            raise ValueError("task_id must not be blank")
        if not source_event_id:
            raise ValueError("source_event_id must not be blank")
        if not instrument:
            raise ValueError("instrument must not be blank")
        if not isinstance(self.mode, TradingMode):
            raise ValueError("mode must be a TradingMode")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "source_event_id", source_event_id)
        object.__setattr__(self, "instrument", instrument)


@dataclass(frozen=True)
class CanonicalTradingTask:
    """Explicit execution intent, separate from tracking and event observation.

    Creating or observing a tracked instrument/event never creates this object.
    Only an APPROVED task may be projected into execution authority for the
    Strategy -> Risk -> PAPER/LIVE path.
    """

    task_id: str
    tracked_event_id: str
    source_event_id: str
    instrument: str
    mode: TradingMode
    state: TradingTaskState
    created_by: str
    created_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    cancelled_by: str | None = None
    cancelled_at: datetime | None = None

    def __post_init__(self) -> None:
        task_id = self.task_id.strip()
        tracked_event_id = self.tracked_event_id.strip()
        source_event_id = self.source_event_id.strip()
        instrument = self.instrument.strip().upper()
        created_by = self.created_by.strip()
        if not task_id:
            raise ValueError("task_id must not be blank")
        if not tracked_event_id:
            raise ValueError("tracked_event_id must not be blank")
        if not source_event_id:
            raise ValueError("source_event_id must not be blank")
        if not instrument:
            raise ValueError("instrument must not be blank")
        if not isinstance(self.mode, TradingMode):
            raise ValueError("mode must be a TradingMode")
        if not isinstance(self.state, TradingTaskState):
            raise ValueError("state must be a TradingTaskState")
        if not created_by:
            raise ValueError("created_by must not be blank")
        _require_aware(self.created_at, "created_at")

        approved_by = self.approved_by.strip() if self.approved_by else None
        cancelled_by = self.cancelled_by.strip() if self.cancelled_by else None
        if self.approved_at is not None:
            _require_aware(self.approved_at, "approved_at")
        if self.cancelled_at is not None:
            _require_aware(self.cancelled_at, "cancelled_at")

        if self.state is TradingTaskState.PENDING:
            if approved_by is not None or self.approved_at is not None:
                raise ValueError("pending trading task must not contain approval metadata")
            if cancelled_by is not None or self.cancelled_at is not None:
                raise ValueError("pending trading task must not contain cancellation metadata")
        elif self.state is TradingTaskState.APPROVED:
            if approved_by is None or self.approved_at is None:
                raise ValueError("approved trading task requires approval metadata")
            if cancelled_by is not None or self.cancelled_at is not None:
                raise ValueError("approved trading task must not contain cancellation metadata")
        elif self.state is TradingTaskState.CANCELLED:
            if cancelled_by is None or self.cancelled_at is None:
                raise ValueError("cancelled trading task requires cancellation metadata")

        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "tracked_event_id", tracked_event_id)
        object.__setattr__(self, "source_event_id", source_event_id)
        object.__setattr__(self, "instrument", instrument)
        object.__setattr__(self, "created_by", created_by)
        object.__setattr__(self, "approved_by", approved_by)
        object.__setattr__(self, "cancelled_by", cancelled_by)

    def execution_context(self) -> CanonicalTradingTaskExecutionContext:
        if self.state is not TradingTaskState.APPROVED:
            raise ValueError("canonical trading task is not approved")
        return CanonicalTradingTaskExecutionContext(
            task_id=self.task_id,
            source_event_id=self.source_event_id,
            instrument=self.instrument,
            mode=self.mode,
        )


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
