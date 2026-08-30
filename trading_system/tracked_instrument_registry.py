from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TrackedInstrumentRecord:
    id: str
    instrument: str
    market: str
    company_name: str
    sources: tuple[str, ...]
    active: bool
    created_by: str
    updated_by: str


def _record_from_row(row: dict[str, Any]) -> TrackedInstrumentRecord:
    return TrackedInstrumentRecord(
        id=str(row["id"]),
        instrument=str(row["instrument"]),
        market=str(row.get("market") or ""),
        company_name=str(row.get("company_name") or ""),
        sources=tuple(str(item) for item in (row.get("sources") or ())),
        active=bool(row["active"]),
        created_by=str(row.get("created_by") or ""),
        updated_by=str(row.get("updated_by") or ""),
    )


class SupabaseTrackedInstrumentRegistry:
    """Canonical persistence boundary for instrument tracking only."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "SupabaseTrackedInstrumentRegistry":
        from supabase import create_client

        url = os.environ.get("MARKETAI_SUPABASE_URL")
        key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
        if not url or not key:
            raise RuntimeError(
                "MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY are required"
            )
        return cls(create_client(url, key))

    def list_active(self) -> list[TrackedInstrumentRecord]:
        response = (
            self.client.table("tracked_instruments")
            .select("*")
            .eq("active", True)
            .order("instrument")
            .order("market")
            .execute()
        )
        data = response.data
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise RuntimeError("tracked_instruments read returned invalid data")
        return [_record_from_row(row) for row in data]

    def upsert(
        self,
        *,
        instrument: str,
        company_name: str,
        market: str,
        source: str,
        actor: str,
    ) -> TrackedInstrumentRecord:
        response = self.client.rpc(
            "upsert_tracked_instrument",
            {
                "input_instrument": instrument,
                "input_company_name": company_name,
                "input_market": market,
                "input_source": source,
                "input_actor": actor,
            },
        ).execute()
        data = response.data
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict):
            raise RuntimeError("upsert_tracked_instrument returned no row")
        return _record_from_row(row)
