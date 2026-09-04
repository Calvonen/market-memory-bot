from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from trading_system.canonical_tracked_event_ingress import SupabaseCanonicalTrackedEventIngress
from trading_system.etoro_instrument_resolver import (
    EtoroInstrumentResolver,
    InstrumentResolutionRequest,
)
from trading_system.etoro_market_data import EtoroMarketDataProvider
from trading_system.market_session_profile import (
    GROUNDED_MARKET_SESSION_PROFILES,
    resolve_market_session_profile,
)
from trading_system.session_calendar_adapter import confirmed_session_opens
from trading_system.tracked_event_repository import TrackedEventTimeStatus
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument
from trading_system.tracked_instrument_registry import SupabaseTrackedInstrumentRegistry


_LOOKAHEAD_DAYS = 14
_ACTOR = "market-open-registration-cli"


def _next_grounded_open(*, etoro_market: str, now: datetime) -> tuple[date, datetime]:
    profile = resolve_market_session_profile(
        etoro_market,
        profiles=GROUNDED_MARKET_SESSION_PROFILES,
    )
    local_date = now.astimezone(ZoneInfo(profile.market_timezone)).date()
    opens = confirmed_session_opens(
        profile,
        start_date=local_date,
        end_date=local_date + timedelta(days=_LOOKAHEAD_DAYS),
    )
    future = [(session_date, open_at) for session_date, open_at in opens if open_at > now]
    if not future:
        raise RuntimeError("no grounded exchange session open found in registration horizon")
    return future[0]


def register_market_open_event(
    *,
    instrument: str,
    company_name: str,
    market: str,
    apply: bool,
    now: datetime | None = None,
) -> dict[str, str]:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    normalized_instrument = instrument.strip().upper()
    normalized_company = company_name.strip()
    normalized_market = market.strip()
    if not normalized_instrument or not normalized_company or not normalized_market:
        raise ValueError("instrument, company_name and market are required")

    resolver = EtoroInstrumentResolver(EtoroMarketDataProvider.from_env())
    resolved = resolver.resolve(
        InstrumentResolutionRequest(
            instrument=normalized_instrument,
            company_name=normalized_company,
            market=normalized_market,
        )
    )
    if resolved is None:
        raise RuntimeError("eToro resolution failed or was ambiguous")
    if resolved.symbol.strip().upper() != normalized_instrument:
        raise RuntimeError("resolved eToro symbol differs from requested canonical instrument")
    resolve_market_session_profile(
        resolved.market,
        profiles=GROUNDED_MARKET_SESSION_PROFILES,
    )
    event_date, open_at = _next_grounded_open(etoro_market=resolved.market, now=timestamp)
    external_key = f"market-open:{normalized_instrument}:{event_date.isoformat()}"

    preview = {
        "instrument": normalized_instrument,
        "company_name": normalized_company,
        "tracked_market": normalized_market,
        "etoro_market": resolved.market,
        "etoro_instrument_id": str(resolved.instrument_id),
        "event_date": event_date.isoformat(),
        "event_at_utc": open_at.astimezone(UTC).isoformat(),
        "external_key": external_key,
        "action": "preview",
    }
    if not apply:
        return preview

    registry = SupabaseTrackedInstrumentRegistry.from_env()
    record = registry.upsert(
        instrument=normalized_instrument,
        company_name=normalized_company,
        market=normalized_market,
        source="manual",
        actor=_ACTOR,
    )
    if not record.active:
        raise RuntimeError("canonical tracked instrument is not active after registration")
    if record.instrument.strip().upper() != normalized_instrument:
        raise RuntimeError("canonical tracked instrument symbol differs after registration")

    tracked = TrackedEtoroInstrument(
        tracked_instrument_id=record.id,
        instrument=record.instrument,
        market=record.market,
        etoro_instrument_id=resolved.instrument_id,
        etoro_symbol=resolved.symbol,
        etoro_display_name=resolved.display_name,
        etoro_market=resolved.market,
    )
    ingress = SupabaseCanonicalTrackedEventIngress.from_env()
    result = ingress.register_for_tracked_instrument(
        tracked,
        company_name=normalized_company,
        source="manual",
        external_key=external_key,
        kind="market_open",
        title=f"{normalized_company} market open",
        event_at=open_at,
        event_date=event_date,
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        actor=_ACTOR,
    )
    return {
        **preview,
        "tracked_instrument_id": result.tracked_instrument_id,
        "tracked_event_id": result.event_id,
        "source_event_id": f"tracked:{result.event_id}",
        "action": result.action,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview or register one canonical grounded market-open event."
    )
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--market", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the tracked instrument/event. Without this flag the command is read-only.",
    )
    args = parser.parse_args()
    result = register_market_open_event(
        instrument=args.instrument,
        company_name=args.company_name,
        market=args.market,
        apply=args.apply,
    )
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
