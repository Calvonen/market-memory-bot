from __future__ import annotations

import argparse
from datetime import datetime

from trading_system.tracked_event_repository import (
    SupabaseTrackedEventRepository,
    TrackedEventTimeStatus,
)


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("event timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("event timestamp must include a timezone/UTC offset")
    return parsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create/update one persistent MarketAI tracked event through the canonical "
            "Supabase RPC. This controls observation only; it never creates a trade."
        )
    )
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--company-name", default="")
    parser.add_argument("--market", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--external-key", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--event-at", required=True, type=_aware_datetime)
    parser.add_argument(
        "--event-time-status",
        required=True,
        choices=[item.value for item in TrackedEventTimeStatus],
    )
    parser.add_argument("--actor", required=True)
    parser.add_argument("--calendar-event-id", default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo = SupabaseTrackedEventRepository.from_env()
    event, action = repo.upsert(
        company_name=args.company_name,
        instrument=args.instrument,
        market=args.market,
        source=args.source,
        external_key=args.external_key,
        kind=args.kind,
        title=args.title,
        event_at=args.event_at,
        event_time_status=TrackedEventTimeStatus(args.event_time_status),
        actor=args.actor,
        calendar_event_id=args.calendar_event_id,
    )
    print(
        f"{action}: id={event.event_id} instrument={event.instrument} "
        f"event_at={event.event_at.isoformat()} time_status={event.event_time_status.value} "
        f"status={event.status.value}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
