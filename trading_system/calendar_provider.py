from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Protocol

import requests


@dataclass(frozen=True)
class CalendarCandidate:
    """One upcoming event surfaced by a calendar provider or entered manually.

    Deliberately minimal for this MVP stage - see docs on the calendar/
    watchlist storage boundary for why time-of-day, release URLs, consensus
    and KPI-level detail are not part of this shape. `event_type` is a plain
    string, not an enum pinned to "earnings" - a manually-entered event (e.g.
    a production report that no calendar provider tracks) is exactly as valid
    a candidate as a provider-sourced earnings date.

    `occurrence_key` disambiguates *which* recurrence of
    (instrument, event_type, source) this is - e.g. "2026Q3" vs. "2026Q4"
    for two quarterly earnings releases of the same company. It is
    deliberately never `scheduled_date` itself: a provider is free to move a
    still-candidate occurrence's date on a later sync (see
    CalendarEventRepository.sync_candidates()), so identity must survive
    that, while a genuinely different occurrence (next quarter) must not
    collide with an already-tracked one. Whoever constructs a
    CalendarCandidate owns picking this value - the repository only ever
    treats it as an opaque identity fragment.
    """

    company_name: str
    instrument: str
    market: str
    event_type: str
    scheduled_date: date
    source: str
    occurrence_key: str


class EarningsCalendarProvider(Protocol):
    """Storage-agnostic upstream data source for upcoming calendar candidates.

    Swapping the data source (Finnhub today, something else later) must never
    require touching CalendarEventRepository or the API layer - both only
    ever see CalendarCandidate values, never a provider-specific response
    shape.
    """

    name: str

    def fetch_upcoming(self, from_date: date, to_date: date) -> tuple[CalendarCandidate, ...]: ...


def _finnhub_occurrence_key(row: dict[str, Any], scheduled_date: date) -> str:
    """Derives a stable occurrence identity for one Finnhub earnings row.

    Finnhub's /calendar/earnings rows normally carry integer `year`/
    `quarter` fields identifying the fiscal period the release covers - that
    is what actually distinguishes "AAPL Q3" from "AAPL Q4", independent of
    `date` (which the row's date field, and therefore this occurrence's
    scheduled_date, can still be revised across syncs without becoming a
    different occurrence). Only if a row is missing either field do we fall
    back to keying on the date itself - an accepted, documented limitation:
    in that fallback-only case a later date revision of the *same*
    occurrence would look like a new one, because there is nothing else in
    the row to identify it by.
    """
    year = row.get("year")
    quarter = row.get("quarter")
    if isinstance(year, int) and isinstance(quarter, int):
        return f"{year}Q{quarter}"
    return f"date:{scheduled_date.isoformat()}"


def _map_finnhub_row(row: dict[str, Any]) -> CalendarCandidate | None:
    """Maps one row of Finnhub's /calendar/earnings response.

    Finnhub's earnings-calendar endpoint only reliably provides `symbol` and
    `date` - not a company display name, exchange, or country. `name` /
    `exchange` / `country` are read defensively in case a plan/response
    variant includes them, but a row missing everything but symbol+date is
    still a perfectly usable candidate: the instrument ticker is itself
    meaningful, and market/company_name fall back rather than reject the row.
    Only a missing/unparseable symbol or date makes a row unusable.
    """
    symbol = row.get("symbol")
    raw_date = row.get("date")
    if not symbol or not raw_date:
        return None
    try:
        scheduled_date = date.fromisoformat(str(raw_date))
    except ValueError:
        return None

    company_name = str(row.get("name") or symbol)
    market = str(row.get("exchange") or row.get("country") or "Unknown")

    return CalendarCandidate(
        company_name=company_name,
        instrument=str(symbol),
        market=market,
        event_type="earnings",
        scheduled_date=scheduled_date,
        source="finnhub",
        occurrence_key=_finnhub_occurrence_key(row, scheduled_date),
    )


class FinnhubEarningsCalendarProvider:
    """First EarningsCalendarProvider adapter, backed by Finnhub.

    The API key is read only from the backend environment
    (`FINNHUB_API_KEY`) - this class is never imported by, or shipped to,
    the Expo app.
    """

    name = "finnhub"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://finnhub.io/api/v1",
        timeout_seconds: int = 30,
        http_get: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY") or ""
        if not self.api_key:
            raise RuntimeError("FINNHUB_API_KEY is required for FinnhubEarningsCalendarProvider")
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        # Injectable for tests - never hits the network unless the real
        # requests.get is used (the default).
        self._http_get = http_get or requests.get

    @classmethod
    def from_env(cls) -> "FinnhubEarningsCalendarProvider":
        return cls()

    def fetch_upcoming(self, from_date: date, to_date: date) -> tuple[CalendarCandidate, ...]:
        try:
            response = self._http_get(
                f"{self.base_url}/calendar/earnings",
                params={
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    "token": self.api_key,
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Finnhub calendar request failed: {exc}") from exc

        if not response.ok:
            raise RuntimeError(f"Finnhub calendar HTTP {response.status_code}: {response.text[:2000]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Finnhub calendar returned invalid JSON") from exc

        rows = data.get("earningsCalendar") or [] if isinstance(data, dict) else []
        candidates = [mapped for row in rows if (mapped := _map_finnhub_row(row)) is not None]
        return tuple(candidates)
