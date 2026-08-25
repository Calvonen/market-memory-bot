from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import exchange_calendars as xcals
import requests

from trading_system.calendar_repository import CalendarEvent
from trading_system.tracked_event_repository import TrackedEventTimeStatus


class CalendarRuntimeTimingUnavailable(RuntimeError):
    """A calendar row cannot be promoted without inventing an event timestamp."""


@dataclass(frozen=True)
class CalendarRuntimeTiming:
    event_at: datetime
    event_time_status: TrackedEventTimeStatus
    provider_timing: str


_FISCAL_OCCURRENCE_RE = re.compile(r"^(\d{4})Q([1-4])$")


class FinnhubCalendarRuntimeTimingResolver:
    """Resolve a safe effective runtime timestamp from Finnhub earnings timing.

    Finnhub supplies a coarse ``hour`` classification rather than an exact
    release timestamp. For US earnings we therefore anchor BMO events to the
    actual XNYS session open and AMC events immediately after the actual XNYS
    session close. Exchange-calendar timestamps own DST, holidays and early
    closes. During-market-hours or missing/ambiguous timing fails closed.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://finnhub.io/api/v1",
        timeout_seconds: int = 30,
        http_get: Callable[..., Any] | None = None,
        calendar_loader: Callable[[str], Any] = xcals.get_calendar,
    ) -> None:
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY") or ""
        if not self.api_key:
            raise RuntimeError("FINNHUB_API_KEY is required for calendar runtime timing")
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self._http_get = http_get or requests.get
        self._calendar_loader = calendar_loader

    @classmethod
    def from_env(cls) -> "FinnhubCalendarRuntimeTimingResolver":
        return cls()

    @staticmethod
    def _row_matches_occurrence(row: dict[str, Any], event: CalendarEvent) -> bool:
        if str(row.get("symbol") or "").strip().upper() != event.instrument.upper():
            return False
        if str(row.get("date") or "").strip() != event.scheduled_date.isoformat():
            return False

        occurrence_match = _FISCAL_OCCURRENCE_RE.fullmatch(event.occurrence_key)
        if occurrence_match is None:
            return True
        year, quarter = occurrence_match.groups()
        return row.get("year") == int(year) and row.get("quarter") == int(quarter)

    @staticmethod
    def _as_aware_utc(value: Any, *, label: str) -> datetime:
        resolved = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
        if not isinstance(resolved, datetime) or resolved.tzinfo is None or resolved.utcoffset() is None:
            raise CalendarRuntimeTimingUnavailable(
                f"exchange calendar returned an invalid {label} timestamp"
            )
        return resolved.astimezone(UTC)

    def resolve(self, event: CalendarEvent) -> CalendarRuntimeTiming:
        if event.source != "finnhub" or event.event_type != "earnings":
            raise CalendarRuntimeTimingUnavailable(
                "calendar runtime timing is only grounded for Finnhub earnings"
            )
        if event.market != "USA":
            raise CalendarRuntimeTimingUnavailable(
                f"calendar runtime timing is not grounded for market {event.market!r}"
            )

        try:
            response = self._http_get(
                f"{self.base_url}/calendar/earnings",
                params={
                    "from": event.scheduled_date.isoformat(),
                    "to": event.scheduled_date.isoformat(),
                    "symbol": event.instrument,
                    "token": self.api_key,
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise CalendarRuntimeTimingUnavailable(
                f"Finnhub timing request failed: {exc}"
            ) from exc

        if not response.ok:
            raise CalendarRuntimeTimingUnavailable(
                f"Finnhub timing HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CalendarRuntimeTimingUnavailable(
                "Finnhub timing returned invalid JSON"
            ) from exc

        rows = payload.get("earningsCalendar") if isinstance(payload, dict) else None
        matches = [
            row
            for row in (rows or [])
            if isinstance(row, dict) and self._row_matches_occurrence(row, event)
        ]
        if len(matches) != 1:
            raise CalendarRuntimeTimingUnavailable(
                "Finnhub timing row was missing or ambiguous for this calendar occurrence"
            )

        provider_timing = str(matches[0].get("hour") or "").strip().lower()
        if provider_timing not in {"bmo", "amc"}:
            raise CalendarRuntimeTimingUnavailable(
                f"Finnhub timing {provider_timing or 'missing'!r} is not precise enough for runtime promotion"
            )

        calendar = self._calendar_loader("XNYS")
        sessions = calendar.sessions_in_range(
            event.scheduled_date.isoformat(), event.scheduled_date.isoformat()
        )
        if len(sessions) != 1:
            raise CalendarRuntimeTimingUnavailable(
                "scheduled earnings date is not exactly one XNYS trading session"
            )
        session = sessions[0]

        if provider_timing == "bmo":
            event_at = self._as_aware_utc(calendar.session_open(session), label="session open")
        else:
            close_at = self._as_aware_utc(calendar.session_close(session), label="session close")
            # Strict pre-event selection requires session_close < event_at.
            # One microsecond keeps the timing conservative while making the
            # just-closed session eligible as immutable pre-event context.
            event_at = close_at + timedelta(microseconds=1)

        return CalendarRuntimeTiming(
            event_at=event_at,
            event_time_status=TrackedEventTimeStatus.ESTIMATED,
            provider_timing=provider_timing,
        )
