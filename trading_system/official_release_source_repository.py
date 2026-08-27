from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


_ALLOWED_SOURCE_KINDS = {"direct_url", "results_page"}


class OfficialReleaseSourceVersionConflict(RuntimeError):
    pass


class OfficialReleaseSourceEventNotFound(RuntimeError):
    pass


def _raise_official_release_source_write_error(
    exc: Exception, *, operation: str
) -> None:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    message_text = str(message) if message is not None else str(exc)
    if code == "40001" and "version_conflict:" in message_text:
        raise OfficialReleaseSourceVersionConflict(
            "official release source version conflict"
        ) from exc
    if code == "P0002" or "event_not_found:" in message_text:
        raise OfficialReleaseSourceEventNotFound(
            "official release source event not found"
        ) from exc
    raise RuntimeError(f"official release source {operation} failed") from exc


def _is_valid_host(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass

    if len(hostname) > 253 or hostname.endswith(".."):
        return False
    normalized_hostname = hostname[:-1] if hostname.endswith(".") else hostname
    labels = normalized_hostname.split(".")
    if not labels or any(not label for label in labels):
        return False
    for label in labels:
        if len(label) > 63:
            return False
        if label[0] == "-" or label[-1] == "-":
            return False
        if not all(ch.isalnum() or ch == "-" for ch in label):
            return False
    return True


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
            or not _is_valid_host(parsed.hostname)
            or "@" in parsed.netloc
            or (parsed_port is not None and not 1 <= parsed_port <= 65535)
        ):
            raise ValueError("official release source_url must be an absolute HTTPS URL with a valid host and no credentials")
        if self.version is not None and self.version < 1:
            raise ValueError("official release source version must be positive")

        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "source_title", source_title)


@dataclass(frozen=True)
class OfficialReleaseSourceState:
    source: OfficialReleaseSource | None
    version: int


class SupabaseOfficialReleaseSourceRepository:
    """Canonical control surface for user-approved official release sources."""

    TABLE = "event_official_release_sources"
    STATE_RPC = "get_audited_official_release_source_state"
    SET_RPC = "set_event_official_release_source_approved"
    CLEAR_RPC = "clear_event_official_release_source_approved"

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

    @staticmethod
    def _canonical_event_id(event_id: str) -> str:
        canonical_event_id = event_id.strip()
        if not canonical_event_id:
            raise ValueError("event_id is required")
        return canonical_event_id

    @staticmethod
    def _canonical_actor(actor: str) -> str:
        canonical_actor = actor.strip()
        if not canonical_actor:
            raise ValueError("actor is required")
        if len(canonical_actor) > 200:
            raise ValueError("actor is too long")
        return canonical_actor

    def _get_state_row(self, event_id: str) -> dict[str, Any]:
        canonical_event_id = self._canonical_event_id(event_id)
        response = self.client.rpc(
            self.STATE_RPC, {"input_event_id": canonical_event_id}
        ).execute()
        rows = response.data or []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise RuntimeError("official release source repository returned an invalid canonical row")
        row = rows[0]
        return {
            "event_id": row.get("out_event_id"),
            "source_kind": row.get("out_source_kind"),
            "source_url": row.get("out_source_url"),
            "source_title": row.get("out_source_title"),
            "is_active": row.get("out_is_active"),
            "version": row.get("out_version"),
        }

    def get_state(self, event_id: str) -> OfficialReleaseSourceState:
        row = self._get_state_row(event_id)
        try:
            version = int(row["version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("official release source row is malformed") from exc
        if version < 0:
            raise RuntimeError("official release source row is malformed")
        if version == 0:
            if (
                row.get("is_active") is False
                and row.get("source_kind") is None
                and row.get("source_url") is None
                and row.get("source_title") is None
            ):
                return OfficialReleaseSourceState(source=None, version=0)
            raise RuntimeError("official release source row is malformed")
        if row.get("is_active") is True:
            source = self._from_row(row)
            if source.version != version:
                raise RuntimeError("official release source row is malformed")
            return OfficialReleaseSourceState(source=source, version=version)
        if (
            row.get("is_active") is False
            and row.get("source_kind") is None
            and row.get("source_url") is None
            and row.get("source_title") is None
        ):
            return OfficialReleaseSourceState(source=None, version=version)
        raise RuntimeError("official release source row is malformed")

    def get(self, event_id: str) -> OfficialReleaseSource | None:
        return self.get_state(event_id).source

    def get_version(self, event_id: str) -> int:
        return self.get_state(event_id).version

    def set(
        self,
        source: OfficialReleaseSource,
        *,
        expected_version: int,
        actor: str,
    ) -> OfficialReleaseSource:
        if expected_version < 0:
            raise ValueError("expected_version must be zero or positive")
        canonical_actor = self._canonical_actor(actor)
        try:
            response = self.client.rpc(
                self.SET_RPC,
                {
                    "input_event_id": source.event_id,
                    "input_source_kind": source.source_kind,
                    "input_source_url": source.source_url,
                    "input_source_title": source.source_title,
                    "input_expected_version": expected_version,
                    "input_actor": canonical_actor,
                },
            ).execute()
        except Exception as exc:
            _raise_official_release_source_write_error(exc, operation="write")
            raise AssertionError("unreachable")
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

    def clear(
        self,
        event_id: str,
        *,
        expected_version: int,
        actor: str,
    ) -> int:
        canonical_event_id = self._canonical_event_id(event_id)
        canonical_actor = self._canonical_actor(actor)
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        try:
            response = self.client.rpc(
                self.CLEAR_RPC,
                {
                    "input_event_id": canonical_event_id,
                    "input_expected_version": expected_version,
                    "input_actor": canonical_actor,
                },
            ).execute()
        except Exception as exc:
            _raise_official_release_source_write_error(exc, operation="clear")
            raise AssertionError("unreachable")
        try:
            new_version = int(response.data)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("official release source clear did not return the new version") from exc
        if new_version <= expected_version:
            raise RuntimeError("official release source clear did not advance the version")
        return new_version
