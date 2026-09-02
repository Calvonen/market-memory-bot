from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from trading_system.models import TradingMode
from trading_system.trading_task import CanonicalTradingTask, TradingTaskState
from trading_system.tracked_event_paper_bridge import CanonicalTradingTaskExecutionContext


class SupabaseTradingTaskRepository:
    """Canonical read/control boundary for explicit trading execution intent."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseTradingTaskRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def create_pending(
        self,
        *,
        tracked_event_id: str,
        source_event_id: str,
        instrument: str,
        mode: TradingMode,
        actor: str,
    ) -> CanonicalTradingTask:
        if not isinstance(mode, TradingMode):
            raise ValueError("mode must be a TradingMode")
        response = self.client.rpc(
            "create_trading_task",
            {
                "input_tracked_event_id": tracked_event_id,
                "input_source_event_id": source_event_id,
                "input_instrument": instrument,
                "input_mode": mode.value,
                "input_actor": actor,
            },
        ).execute()
        return self._one(response.data, "create_trading_task")

    def approve(self, *, task_id: str, actor: str) -> CanonicalTradingTask:
        response = self.client.rpc(
            "approve_trading_task",
            {"input_task_id": task_id, "input_actor": actor},
        ).execute()
        return self._one(response.data, "approve_trading_task")

    def approve_paper_permission(
        self,
        *,
        tracked_event_id: str,
        source_event_id: str,
        instrument: str,
        actor: str,
        expected_expectation_version: int,
        max_position_value_usd: float,
    ) -> dict[str, Any]:
        response = self.client.rpc(
            "approve_paper_trading_task_for_event",
            {
                "input_tracked_event_id": tracked_event_id,
                "input_source_event_id": source_event_id,
                "input_instrument": instrument,
                "input_actor": actor,
                "input_expected_expectation_version": expected_expectation_version,
                "input_max_position_value_usd": max_position_value_usd,
            },
        ).execute()
        return self._one_row(response.data, "approve_paper_trading_task_for_event")

    def cancel(self, *, task_id: str, actor: str) -> CanonicalTradingTask:
        response = self.client.rpc(
            "cancel_trading_task",
            {"input_task_id": task_id, "input_actor": actor},
        ).execute()
        return self._one(response.data, "cancel_trading_task")

    def get(self, task_id: str) -> CanonicalTradingTask | None:
        rows = (
            self.client.table("trading_tasks")
            .select("*")
            .eq("id", task_id)
            .limit(2)
            .execute()
            .data
            or []
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError("canonical trading task read returned ambiguous rows")
        return self._from_row(rows[0])

    def get_active_row_for_event_mode(
        self,
        *,
        tracked_event_id: str,
        mode: TradingMode,
    ) -> dict[str, Any] | None:
        if not isinstance(mode, TradingMode):
            raise ValueError("mode must be a TradingMode")
        rows = (
            self.client.table("trading_tasks")
            .select("*")
            .eq("tracked_event_id", tracked_event_id)
            .eq("mode", mode.value)
            .in_("state", [TradingTaskState.PENDING.value, TradingTaskState.APPROVED.value])
            .limit(2)
            .execute()
            .data
            or []
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError("active trading task read returned ambiguous rows")
        return dict(rows[0])

    def execution_context(self, task_id: str) -> CanonicalTradingTaskExecutionContext:
        task = self.get(task_id)
        if task is None:
            raise LookupError("canonical trading task not found")
        if task.state is not TradingTaskState.APPROVED:
            raise ValueError("canonical trading task is not approved")
        return CanonicalTradingTaskExecutionContext(
            task_id=task.task_id,
            source_event_id=task.source_event_id,
            instrument=task.instrument,
            mode=task.mode,
            max_position_value_usd=task.max_position_value_usd,
        )

    @classmethod
    def _one(cls, data: Any, operation: str) -> CanonicalTradingTask:
        return cls._from_row(cls._one_row(data, operation))

    @staticmethod
    def _one_row(data: Any, operation: str) -> dict[str, Any]:
        if isinstance(data, dict):
            rows = [data]
        else:
            rows = data or []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError(f"{operation} returned {len(rows)} rows")
        return dict(rows[0])

    @staticmethod
    def _from_row(row: dict[str, Any]) -> CanonicalTradingTask:
        try:
            raw_cap = row.get("max_position_value_usd")
            return CanonicalTradingTask(
                task_id=str(row["id"]),
                tracked_event_id=str(row["tracked_event_id"]),
                source_event_id=str(row["source_event_id"]),
                instrument=str(row["instrument"]),
                mode=TradingMode(str(row["mode"])),
                state=TradingTaskState(str(row["state"])),
                created_by=str(row["created_by"]),
                created_at=_parse_datetime(row["created_at"]),
                approved_by=row.get("approved_by"),
                approved_at=_parse_datetime(row["approved_at"]) if row.get("approved_at") else None,
                cancelled_by=row.get("cancelled_by"),
                cancelled_at=_parse_datetime(row["cancelled_at"]) if row.get("cancelled_at") else None,
                max_position_value_usd=float(raw_cap) if raw_cap is not None else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("malformed canonical trading task row") from exc


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp must be text or datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)
