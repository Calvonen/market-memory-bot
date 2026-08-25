from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


_ALLOWED_SOURCE_KINDS = {"direct_url", "results_page"}


@dataclass(frozen=True)
class OfficialReleaseSource:
    event_id: str
    source_kind: str
    source_url: str
    source_title: str | None = None

    def __post_init__(self) -> None:
        event_id = self.event_id.strip()
        source_kind = self.source_kind.strip()
        source_url = self.source_url.strip()
        source_title = self.source_title.strip() if self.source_title else None

        if not event_id:
            raise ValueError("official release source event_id is required")
        if source_kind not in _ALLOWED_SOURCE_KINDS:
            raise ValueError("official release source_kind must be direct_url or results_page")
        parsed = urlparse(source_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("official release source_url must be an absolute HTTPS URL without credentials")

        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "source_title", source_title)


class SupabaseOfficialReleaseSourceRepository:
    """Canonical control surface for user-approved official release sources."""

    TABLE = "event_official_release_sources"

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseOfficialReleaseSourceRepository":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def get(self, event_id: str) -> OfficialReleaseSource | None:
        canonical_event_id = event_id.strip()
        if not canonical_event_id:
            raise ValueError("event_id is required")
        response = (
            self.client.table(self.TABLE)
            .select("event_id,source_kind,source_url,source_title")
            .eq("event_id", canonical_event_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("official release source repository returned an invalid canonical row")
        row = rows[0]
        try:
            return OfficialReleaseSource(
                event_id=str(row["event_id"]),
                source_kind=str(row["source_kind"]),
                source_url=str(row["source_url"]),
                source_title=(str(row["source_title"]) if row.get("source_title") is not None else None),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("official release source row is malformed") from exc

    def set(self, source: OfficialReleaseSource) -> OfficialReleaseSource:
        payload = {
            "event_id": source.event_id,
            "source_kind": source.source_kind,
            "source_url": source.source_url,
            "source_title": source.source_title,
        }
        response = (
            self.client.table(self.TABLE)
            .upsert(payload, on_conflict="event_id")
            .select("event_id,source_kind,source_url,source_title")
            .execute()
        )
        rows = response.data or []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("official release source write did not return exactly one canonical row")
        return OfficialReleaseSource(
            event_id=str(rows[0]["event_id"]),
            source_kind=str(rows[0]["source_kind"]),
            source_url=str(rows[0]["source_url"]),
            source_title=(str(rows[0]["source_title"]) if rows[0].get("source_title") is not None else None),
        )

    def clear(self, event_id: str) -> None:
        canonical_event_id = event_id.strip()
        if not canonical_event_id:
            raise ValueError("event_id is required")
        self.client.table(self.TABLE).delete().eq("event_id", canonical_event_id).execute()
