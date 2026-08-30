from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class TrackedInstrumentProfileInstrumentNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class TrackedInstrumentProfileRecord:
    id: str
    tracked_instrument_id: str
    profile_type: str
    specs: str
    enabled: bool
    created_by: str
    updated_by: str
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


def _record_from_row(row: dict[str, Any]) -> TrackedInstrumentProfileRecord:
    return TrackedInstrumentProfileRecord(
        id=str(row["id"]),
        tracked_instrument_id=str(row["tracked_instrument_id"]),
        profile_type=str(row["profile_type"]),
        specs=str(row.get("specs") or ""),
        enabled=bool(row["enabled"]),
        created_by=str(row.get("created_by") or ""),
        updated_by=str(row.get("updated_by") or ""),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class SupabaseTrackedInstrumentProfileRegistry:
    """Persistence boundary for descriptive tracked-instrument profiles only."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseTrackedInstrumentProfileRegistry":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def list_for_instrument(
        self, tracked_instrument_id: str
    ) -> list[TrackedInstrumentProfileRecord]:
        instrument_id = tracked_instrument_id.strip()
        if not instrument_id:
            raise ValueError("tracked_instrument_id is required")

        identity_response = (
            self.client.table("tracked_instruments")
            .select("id")
            .eq("id", instrument_id)
            .execute()
        )
        identity_data = identity_response.data
        if not isinstance(identity_data, list):
            raise RuntimeError("tracked_instruments identity read returned invalid data")
        if not identity_data:
            raise TrackedInstrumentProfileInstrumentNotFound(instrument_id)
        if (
            len(identity_data) != 1
            or not isinstance(identity_data[0], dict)
            or identity_data[0].get("id") != instrument_id
        ):
            raise RuntimeError("tracked_instruments identity read returned invalid data")

        response = (
            self.client.table("tracked_instrument_profiles")
            .select("*")
            .eq("tracked_instrument_id", instrument_id)
            .order("profile_type")
            .execute()
        )
        data = response.data
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise RuntimeError("tracked_instrument_profiles read returned invalid data")
        return [_record_from_row(row) for row in data]

    def list_for_instruments(
        self, tracked_instrument_ids: list[str]
    ) -> dict[str, list[TrackedInstrumentProfileRecord]]:
        instrument_ids: list[str] = []
        seen: set[str] = set()
        for raw_id in tracked_instrument_ids:
            instrument_id = raw_id.strip()
            if not instrument_id:
                raise ValueError("tracked_instrument_id must be nonblank")
            if instrument_id not in seen:
                seen.add(instrument_id)
                instrument_ids.append(instrument_id)

        if not instrument_ids:
            raise ValueError("at least one tracked_instrument_id is required")
        if len(instrument_ids) > 50:
            raise ValueError("at most 50 tracked_instrument_id values are allowed")

        identity_response = (
            self.client.table("tracked_instruments")
            .select("id")
            .in_("id", instrument_ids)
            .execute()
        )
        identity_data = identity_response.data
        if not isinstance(identity_data, list) or any(
            not isinstance(row, dict) for row in identity_data
        ):
            raise RuntimeError("tracked_instruments batch identity read returned invalid data")

        returned_ids = [row.get("id") for row in identity_data]
        if len(returned_ids) != len(instrument_ids) or set(returned_ids) != set(instrument_ids):
            missing = next((item for item in instrument_ids if item not in returned_ids), None)
            if missing is not None:
                raise TrackedInstrumentProfileInstrumentNotFound(missing)
            raise RuntimeError("tracked_instruments batch identity read returned invalid data")

        profile_response = (
            self.client.table("tracked_instrument_profiles")
            .select("*")
            .in_("tracked_instrument_id", instrument_ids)
            .order("tracked_instrument_id")
            .order("profile_type")
            .execute()
        )
        profile_data = profile_response.data
        if not isinstance(profile_data, list) or any(
            not isinstance(row, dict) for row in profile_data
        ):
            raise RuntimeError("tracked_instrument_profiles batch read returned invalid data")

        records_by_instrument: dict[str, list[TrackedInstrumentProfileRecord]] = {
            instrument_id: [] for instrument_id in instrument_ids
        }
        for row in profile_data:
            instrument_id = row.get("tracked_instrument_id")
            if instrument_id not in records_by_instrument:
                raise RuntimeError("tracked_instrument_profiles batch read returned invalid data")
            records_by_instrument[instrument_id].append(_record_from_row(row))
        return records_by_instrument

    def upsert(
        self,
        *,
        tracked_instrument_id: str,
        profile_type: str,
        specs: str,
        enabled: bool,
        actor: str,
    ) -> TrackedInstrumentProfileRecord:
        try:
            response = self.client.rpc(
                "upsert_tracked_instrument_profile",
                {
                    "input_tracked_instrument_id": tracked_instrument_id,
                    "input_profile_type": profile_type,
                    "input_specs": specs,
                    "input_enabled": enabled,
                    "input_actor": actor,
                },
            ).execute()
        except Exception as exc:
            if "tracked_profile_instrument_not_found" in str(exc):
                raise TrackedInstrumentProfileInstrumentNotFound(
                    tracked_instrument_id
                ) from exc
            raise

        data = response.data
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict):
            raise RuntimeError("upsert_tracked_instrument_profile returned no row")
        return _record_from_row(row)
