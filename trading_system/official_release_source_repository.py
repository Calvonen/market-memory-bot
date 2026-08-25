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
    version: int | None = None

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
        try:
            parsed_port = parsed.port
        except ValueError as exc:
            raise ValueError("official release source_url must use a valid port") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or (parsed_port is not None and not 1 <= parsed_port <= 65535)
        ):
            raise ValueError("official release source_url must be an absolute HTTPS URL with a valid host and no credentials")
        if self.version is not None and self.version < 1:
            raise ValueError("official release source version must be positive")

        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "source_title", source_title)


class SupabaseOfficialReleaseSourceRepository:
    """Canonical control surface for user-approved official release sources."""

    TABLE = "event_official_release_sources"
    SET_RPC = "set_event_official_release_source"
    CLEAR_RPC = "clear_event_official_release_source"

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

    @staticmethod
    def _from_row(row: dict[str, Any]) -> OfficialReleaseSource:
        try:
            version = int(row["version"])
            return OfficialReleaseSource(
                event_id=str(row["event_id"]),
                source_kind=str(row["source_kind"]),
                source_url=str(row["source_url"]),
                source_title=(str(row["source_title"]) if row.get("source_title") is not None else None),
                version=version,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("official release source row is malformed") from exc

    def get(self, event_id: str) -> OfficialReleaseSource | None:
        canonical_event_id = event_id.strip()
        if not canonical_event_id:
            raise ValueError("event_id is required")
        response = (
            self.client.table(self.TABLE)
            .select("event_id,source_kind,source_url,source_title,version")
            .eq("event_id", canonical_event_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("official release source repository returned an invalid canonical row")
        return self._from_row(rows[0])

    def set(
        self,
        source: OfficialReleaseSource,
        *,
        expected_version: int,
    ) -> OfficialReleaseSource:
        if expected_version < 0:
            raise ValueError("expected_version must be zero or positive")
        response = self.client.rpc(
            self.SET_RPC,
            {
                "input_event_id": source.event_id,
                "input_source_kind": source.source_kind,
                "input_source_url": source.source_url,
                "input_source_title": source.source_title,
                "input_expected_version": expected_version,
            },
        ).execute()
        rows = response.data or []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("official release source write did not return exactly one canonical row")
        row = rows[0]
        canonical_row = {
            "event_id": row.get("out_event_id"),
            "source_kind": row.get("out_source_kind"),
            "source_url": row.get("out_source_url"),
            "source_title": row.get("out_source_title"),
            "version": row.get("out_version"),
        }
        return self._from_row(canonical_row)

    def clear(self, event_id: str, *, expected_version: int) -> None:
        canonical_event_id = event_id.strip()
        if not canonical_event_id:
            raise ValueError("event_id is required")
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        response = self.client.rpc(
            self.CLEAR_RPC,
            {
                "input_event_id": canonical_event_id,
                "input_expected_version": expected_version,
            },
        ).execute()
        if response.data is not True:
            raise RuntimeError("official release source clear did not confirm deletion")
