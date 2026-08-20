from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from trading_system.post_release_paper import PostReleasePaperResult


class SupabasePaperTradeRepository:
    """Persist and read the latest paper confirmation state for one AI analysis.

    One row per analysis is intentionally upserted as confirmation evolves from
    waiting to an executed paper order.  This keeps a durable strategy/risk/order
    audit without creating a row every five minutes while waiting for confirmation.
    """

    def __init__(self, client: Any) -> None:
        self.client = client

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
        response = (
            self.client.table("event_paper_trade_runs")
            .select("*")
            .eq("event_id", event_id)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
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

    def claim_event(self, *, event_id: str, analysis_id: str) -> dict[str, Any]:
        response = self.client.rpc(
            "claim_event_paper_run",
            {"input_event_id": event_id, "input_analysis_id": analysis_id},
        ).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError(f"paper event claim returned no owner for {event_id}")
        return rows[0]

    def save_result(
        self,
        *,
        event_id: str,
        expectation_version: int,
        source_document_id: str | None,
        analysis_id: str,
        result: PostReleasePaperResult,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_id": event_id,
            "expectation_version": expectation_version,
            "source_document_id": source_document_id,
            "analysis_id": analysis_id,
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
                "reward_risk": risk.reward_risk,
                "created_at": risk.created_at.isoformat(),
                "proposal_id": proposal.proposal_id,
                "mode": proposal.mode.value,
            }
            if result.pipeline.order is not None:
                order = result.pipeline.order
                payload["paper_order"] = {
                    "order_id": order.order_id,
                    "instrument": order.instrument,
                    "direction": order.direction.value,
                    "quantity": order.quantity,
                    "reference_price": order.reference_price,
                    "status": order.status,
                    "created_at": order.created_at.isoformat(),
                }

        response = self.client.rpc(
            "save_event_paper_trade_result",
            {"input_payload": payload},
        ).execute()
        rows = response.data or []
        # An empty result means the database atomically rejected this write
        # because a terminal row won the race. Reading afterwards is only to
        # return the winner; correctness does not depend on a read-then-write.
        if not rows:
            winner = self.get_for_analysis(analysis_id)
            if winner is None:
                raise RuntimeError(
                    f"paper result write was rejected but analysis {analysis_id} has no persisted row"
                )
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
    ) -> dict[str, Any] | None:
        response = self.client.rpc(
            "expire_event_paper_trade_run",
            {
                "input_event_id": event_id,
                "input_expectation_version": expectation_version,
                "input_source_document_id": source_document_id,
                "input_analysis_id": analysis_id,
                "input_confirmation_deadline_at": confirmation_deadline_at.isoformat(),
                "input_expired_at": expired_at.isoformat(),
            },
        ).execute()
        rows = response.data or []
        return rows[0] if rows else self.get_for_analysis(analysis_id)
