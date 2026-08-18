from __future__ import annotations

import os
from typing import Any

from trading_system.ai_event_analyzer import AIEventAnalysis
from trading_system.release_ingestion import ReleaseDocument


class SupabaseReleaseRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseReleaseRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def find_document(self, event_id: str, content_sha256: str) -> dict[str, Any] | None:
        response = (
            self.client.table("event_source_documents")
            .select("*")
            .eq("event_id", event_id)
            .eq("content_sha256", content_sha256)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None

    def save_document(self, document: ReleaseDocument) -> dict[str, Any]:
        existing = self.find_document(document.event_id, document.content_sha256)
        if existing:
            return existing
        response = (
            self.client.table("event_source_documents")
            .insert(
                {
                    "event_id": document.event_id,
                    "source_type": document.source_type,
                    "source_url": document.source_url,
                    "source_title": document.source_title,
                    "content_sha256": document.content_sha256,
                    "raw_text": document.raw_text,
                }
            )
            .select("*")
            .execute()
        )
        return (response.data or [{}])[0]

    def save_analysis(
        self,
        *,
        event_id: str,
        source_document_id: str,
        expectation_version: int,
        analysis: AIEventAnalysis,
    ) -> dict[str, Any]:
        response = (
            self.client.table("event_ai_analyses")
            .upsert(
                {
                    "event_id": event_id,
                    "source_document_id": source_document_id,
                    "expectation_version": expectation_version,
                    "provider": analysis.provider,
                    "model": analysis.model,
                    "analysis": analysis.payload.model_dump(),
                    "raw_response": analysis.raw_response,
                },
                on_conflict="source_document_id,expectation_version,provider,model",
            )
            .select("*")
            .execute()
        )
        return (response.data or [{}])[0]

    def record_run(
        self,
        *,
        event_id: str,
        provider: str,
        status: str,
        source_url: str | None = None,
        source_document_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.client.table("event_ingestion_runs").insert(
            {
                "event_id": event_id,
                "provider": provider,
                "status": status,
                "source_url": source_url,
                "source_document_id": source_document_id,
                "error_message": error_message,
            }
        ).execute()
