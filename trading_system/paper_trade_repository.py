from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from trading_system.brokers.base import broker_order_payload
from trading_system.post_release_paper import PostReleasePaperResult


class SupabasePaperTradeRepository:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.claim_token = str(uuid4())

    @classmethod
    def from_env(cls) -> "SupabasePaperTradeRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def get_latest_for_event(self, event_id: str) -> dict[str, Any] | None:
        response = self.client.rpc(
            "get_event_paper_trade_state",
            {"input_event_id": event_id},
        ).execute()
        rows = response.data or []
        return rows[0] if rows else None

    def get_for_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        response = (
            self.client.table("event_paper_trade_runs")
            .select("*")
            .eq("analysis_id", analysis_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None

    def claim_event(
        self,
        *,
        event_id: str,
        analysis_id: str,
        lease_seconds: int,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        response = self.client.rpc(
            "claim_event_paper_run",
            {
                "input_event_id": event_id,
                "input_analysis_id": analysis_id,
                "input_claim_token": claim_token or self.claim_token,
                "input_lease_seconds": max(1, lease_seconds),
            },
        ).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError(f"paper event claim returned no owner for {event_id}")
        return rows[0]

    def _completed_order_payload(self, task_id: str) -> dict[str, Any] | None:
        response = (
            self.client.table("event_paper_broker_attempts")
            .select("order_payload")
            .eq("task_id", task_id)
            .eq("status", "completed")
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        payload = rows[0].get("order_payload")
        if not isinstance(payload, dict):
            raise RuntimeError("completed broker attempt is missing canonical order payload")
        return payload

    def save_result(
        self,
        *,
        event_id: str,
        expectation_version: int,
        source_document_id: str | None,
        analysis_id: str,
        result: PostReleasePaperResult,
        claim_token: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_id": event_id,
            "expectation_version": expectation_version,
            "source_document_id": source_document_id,
            "analysis_id": analysis_id,
            "claim_token": claim_token or self.claim_token,
            "task_id": task_id,
            "status": result.status,
            "message": result.message,
            "strategy": None,
            "risk": None,
            "paper_order": None,
            "completed_components": None,
            "confirmation_deadline_at": (
                result.confirmation_deadline_at.isoformat()
                if result.confirmation_deadline_at
                else None
            ),
            "expired_at": result.expired_at.isoformat() if result.expired_at else None,
            "updated_at": datetime.now(UTC).isoformat(),
        }

        if result.completed_components is not None:
            payload["completed_components"] = {
                component.name: {
                    "direction": component.direction.value,
                    "score": component.score,
                    "max_score": component.max_score,
                    "reasons": list(component.reasons),
                }
                for component in result.completed_components
            }

        if result.pipeline is not None:
            strategy = result.pipeline.strategy
            proposal = result.pipeline.proposal
            payload["strategy"] = {
                "decision_id": strategy.decision_id,
                "instrument": strategy.instrument,
                "direction": strategy.direction.value,
                "confidence": strategy.confidence,
                "scores": {
                    "fundamental": strategy.scores.fundamental,
                    "catalyst": strategy.scores.catalyst,
                    "technical": strategy.scores.technical,
                    "market_memory": strategy.scores.market_memory,
                    "news_sentiment": strategy.scores.news_sentiment,
                    "total": strategy.scores.total,
                },
                "rationale": list(strategy.rationale),
                "invalidation": list(strategy.invalidation),
                "long_evidence": strategy.long_evidence,
                "short_evidence": strategy.short_evidence,
                "source_event_id": strategy.source_event_id,
                "created_at": strategy.created_at.isoformat(),
            }
            risk = proposal.risk
            payload["risk"] = {
                "decision_id": risk.decision_id,
                "status": risk.status.value,
                "reasons": list(risk.reasons),
                "max_risk_amount": risk.max_risk_amount,
                "max_position_value": risk.max_position_value,
                "max_quantity": risk.max_quantity,
                "max_fractional_notional_usd": risk.max_fractional_notional_usd,
                "reward_risk": risk.reward_risk,
                "created_at": risk.created_at.isoformat(),
                "proposal_id": proposal.proposal_id,
                "mode": proposal.mode.value,
            }
            if result.pipeline.order is not None:
                terminal_order = broker_order_payload(result.pipeline.order)
                if task_id is not None:
                    completed_order = self._completed_order_payload(task_id)
                    if completed_order is not None:
                        for key in ("order_id", "instrument", "direction", "status"):
                            if str(completed_order.get(key)) != str(terminal_order.get(key)):
                                raise RuntimeError(
                                    "terminal PAPER order differs from completed broker attempt"
                                )
                        terminal_order = completed_order
                payload["paper_order"] = terminal_order

        rpc_name = (
            "save_event_paper_trade_result_for_task"
            if task_id is not None
            else "save_event_paper_trade_result"
        )
        response = self.client.rpc(
            rpc_name,
            {"input_payload": payload},
        ).execute()
        rows = response.data or []
        if not rows:
            winner = self.get_for_analysis(analysis_id)
            if winner is None:
                rejection = {
                    "event_id": event_id,
                    "analysis_id": analysis_id,
                    "status": "waiting_confirmation",
                    "message": "paper result was rejected after the event lease was lost",
                    "write_rejection": "lease_lost",
                }
                if task_id is not None:
                    rejection["task_id"] = task_id
                return rejection
            return winner
        return rows[0]

    def expire_waiting(
        self,
        *,
        event_id: str,
        expectation_version: int,
        source_document_id: str | None,
        analysis_id: str,
        confirmation_deadline_at: datetime,
        expired_at: datetime,
        claim_token: str | None = None,
    ) -> dict[str, Any] | None:
        response = self.client.rpc(
            "expire_event_paper_trade_run",
            {
                "input_event_id": event_id,
                "input_expectation_version": expectation_version,
                "input_source_document_id": source_document_id,
                "input_analysis_id": analysis_id,
                "input_claim_token": claim_token or self.claim_token,
                "input_confirmation_deadline_at": confirmation_deadline_at.isoformat(),
                "input_expired_at": expired_at.isoformat(),
            },
        ).execute()
        rows = response.data or []
        if rows:
            result = rows[0]
            if result.get("status") == "waiting_confirmation":
                return {**result, "expiry_rejection": "lease_conflict"}
            return result
        deadline_response = self.client.rpc(
            "is_event_confirmation_deadline_reached",
            {"input_confirmation_deadline_at": confirmation_deadline_at.isoformat()},
        ).execute()
        deadline_reached = deadline_response.data is True
        winner = self.get_for_analysis(analysis_id)
        result = winner or {
            "event_id": event_id,
            "analysis_id": analysis_id,
            "status": "waiting_confirmation",
            "message": "paper expiry was rejected",
        }
        return {
            **result,
            "expiry_rejection": (
                "lease_conflict" if deadline_reached else "deadline_open"
            ),
        }
