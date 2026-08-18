from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from trading_system.post_release_paper import PostReleasePaperResult


class SupabasePaperTradeRepository:
    """Persist the latest paper confirmation state for one AI analysis.

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
            "updated_at": datetime.now(UTC).isoformat(),
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

        response = (
            self.client.table("event_paper_trade_runs")
            .upsert(payload, on_conflict="analysis_id")
            .select("*")
            .execute()
        )
        return (response.data or [{}])[0]
