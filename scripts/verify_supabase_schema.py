#!/usr/bin/env python3
"""Pre-deploy Supabase schema gate for MarketAI backend runtime dependencies.

The existing strategy-draft and calendar/watchlist checks stay in place. The
persistent tracked-event worker additionally requires the objects introduced by
20260827090000_persistent_tracked_market_events.sql,
20260827091000_tracked_event_schema_gate.sql and
20260827092000_tracked_event_reaction_anchor.sql. Migrations are applied
out-of-band; this script only verifies them before backend/systemd processes are
restarted.
"""

from __future__ import annotations

import os
import sys
from typing import Any

REQUIRED_CHECKS: tuple[tuple[str, str], ...] = (
    ("event_strategy_approvals_table_exists", "event_strategy_approvals table"),
    ("approve_strategy_draft_function_exists", "approve_strategy_draft() function"),
    (
        "insert_next_expectation_version_function_exists",
        "insert_next_expectation_version() function",
    ),
    (
        "schema_version_matches",
        "insert_next_expectation_version() implementation version "
        "(strategy_draft_schema_version() mismatch or missing - a "
        "same-signature but outdated function body is deployed)",
    ),
    ("calendar_events_table_exists", "calendar_events table"),
    ("upsert_calendar_candidate_function_exists", "upsert_calendar_candidate() function"),
    (
        "calendar_candidate_upsert_version_matches",
        "upsert_calendar_candidate() placeholder-preserving implementation version",
    ),
    (
        "transition_calendar_event_status_function_exists",
        "transition_calendar_event_status() function",
    ),
)

REQUIRED_TRACKED_EVENT_CHECKS: tuple[tuple[str, str], ...] = (
    ("tracked_market_events_table_exists", "tracked_market_events table"),
    (
        "tracked_market_event_reactions_table_exists",
        "tracked_market_event_reactions table",
    ),
    (
        "upsert_tracked_market_event_function_exists",
        "upsert_tracked_market_event() function",
    ),
    (
        "capture_tracked_market_event_reference_function_exists",
        "capture_tracked_market_event_reference() function",
    ),
    (
        "capture_tracked_market_event_reaction_anchor_function_exists",
        "capture_tracked_market_event_reaction_anchor() function",
    ),
)

REQUIRED_CALENDAR_CANDIDATE_UPSERT_VERSION = 2


def main() -> int:
    url = os.environ.get("MARKETAI_SUPABASE_URL")
    key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
    if not url or not key:
        print(
            "SCHEMA GATE FAILED: MARKETAI_SUPABASE_URL and MARKETAI_SUPABASE_SECRET_KEY "
            "must both be set to verify the Supabase schema before deploying. Refusing "
            "to proceed - the backend will not be restarted.",
            file=sys.stderr,
        )
        return 1

    try:
        from supabase import create_client
    except Exception as exc:  # pragma: no cover - environment/import failure
        print(
            f"SCHEMA GATE FAILED: could not import the supabase client: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        client = create_client(url, key)
        response = client.rpc("verify_strategy_draft_schema", {}).execute()
    except Exception as exc:
        print(
            "SCHEMA GATE FAILED: could not call verify_strategy_draft_schema(). "
            "Apply the pending strategy/calendar migrations before deploying. "
            f"Underlying error: {exc}",
            file=sys.stderr,
        )
        return 1

    rows = getattr(response, "data", None) or []
    if not rows:
        print(
            "SCHEMA GATE FAILED: verify_strategy_draft_schema() returned no rows.",
            file=sys.stderr,
        )
        return 1

    row: dict[str, Any] = rows[0]
    missing = [label for key_name, label in REQUIRED_CHECKS if not row.get(key_name)]

    deployed_upsert_version = row.get("calendar_candidate_upsert_implementation_version")
    if deployed_upsert_version != REQUIRED_CALENDAR_CANDIDATE_UPSERT_VERSION:
        missing.append(
            "upsert_calendar_candidate() atomic implementation version "
            f"{REQUIRED_CALENDAR_CANDIDATE_UPSERT_VERSION} "
            f"(deployed: {deployed_upsert_version!r})"
        )

    try:
        tracked_response = client.rpc("verify_tracked_event_runtime_schema", {}).execute()
    except Exception as exc:
        print(
            "SCHEMA GATE FAILED: could not call verify_tracked_event_runtime_schema(). "
            "Apply the pending tracked-event migrations before deploying. "
            f"Underlying error: {exc}",
            file=sys.stderr,
        )
        return 1

    tracked_rows = getattr(tracked_response, "data", None) or []
    if not tracked_rows:
        print(
            "SCHEMA GATE FAILED: verify_tracked_event_runtime_schema() returned no rows.",
            file=sys.stderr,
        )
        return 1
    tracked_row: dict[str, Any] = tracked_rows[0]
    missing.extend(
        label for key_name, label in REQUIRED_TRACKED_EVENT_CHECKS if not tracked_row.get(key_name)
    )

    if missing:
        print(
            "SCHEMA GATE FAILED: the following required Supabase objects are missing: "
            + ", ".join(missing)
            + ". Apply the pending migrations under supabase/migrations/ to this "
            "Supabase project before deploying this commit.",
            file=sys.stderr,
        )
        return 1

    print(
        "Supabase schema gate passed: strategy-draft/calendar dependencies and "
        "persistent tracked-event runtime tables/RPCs are present."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
