#!/usr/bin/env python3
"""Pre-deploy Supabase schema gate for the strategy-draft approval flow and
the calendar/watchlist candidate-tracking endpoints.

This backend commit's strategy-draft endpoints
(POST .../strategy-draft/approve, and the admin write endpoint's
apply_partial_update()) depend on Supabase objects added by
supabase/migrations/20260821090000_event_strategy_approvals.sql,
supabase/migrations/20260821140000_shared_expectation_version_lock.sql,
supabase/migrations/20260822090000_verify_strategy_draft_schema.sql, and
supabase/migrations/20260823090000_expectation_write_atomic_response_and_schema_version.sql.
The calendar/watchlist endpoints (GET/POST /api/v1/calendar/*) similarly
depend on objects added by
supabase/migrations/20260824090000_calendar_watchlist_events.sql and
supabase/migrations/20260825090000_calendar_schema_gate.sql (the latter is
what extends verify_strategy_draft_schema() itself with the calendar
checks below), and
supabase/migrations/20260829090000_distinct_atomic_calendar_candidate_upsert_version.sql,
which atomically installs and version-gates the placeholder-preserving
candidate upsert body without depending on the two earlier follow-ups. Those
migrations are applied out-of-band, before merging,
through a separate secure mechanism (the Supabase CLI/dashboard) - this
script and the CI it runs in hold no Postgres-DDL-capable credential and
apply no migrations themselves.

Existence checks alone are not sufficient here: insert_next_expectation_
version() has changed its actual behavior more than once while keeping the
exact same call signature, so "a function with this signature exists" can
be true against an outdated deployment whose body still has the earlier
(buggy) semantics. schema_version_matches closes that gap - it calls
strategy_draft_schema_version(), a small marker function bumped by every
migration that changes this write path's semantics, and compares it
against the exact value this backend commit requires. An older deployment
either has no such function to call (which PostgREST surfaces as this
entire RPC call failing - see "Fails closed" below) or has it returning a
stale value, and either way this check reports it as missing/failed.

This script only *verifies* the resulting schema is present, using the
read-only verify_strategy_draft_schema() RPC and the same
MARKETAI_SUPABASE_URL/MARKETAI_SUPABASE_SECRET_KEY the already-running
backend service itself uses. It never receives a GitHub Actions secret and
is never given the Supabase service-role/admin key inline - see
.github/workflows/deploy-seesam-hub.yml, which sources the same env file
the systemd service already reads before invoking this script.

Fails closed: any problem - missing env vars, a network error, the RPC
itself missing (almost always meaning the migrations above have not been
applied yet), or any individual check coming back false - exits non-zero.
The deploy workflow runs this before restarting the backend and must treat
a non-zero exit here as "do not restart" (see the "Deploy gate: Supabase
schema verification" section of docs/event_configuration_storage.md).
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
            "SCHEMA GATE FAILED: could not call verify_strategy_draft_schema(). This "
            "almost always means the required Supabase migrations "
            "(supabase/migrations/20260821090000_event_strategy_approvals.sql, "
            "20260821140000_shared_expectation_version_lock.sql, "
            "20260822090000_verify_strategy_draft_schema.sql, "
            "20260823090000_expectation_write_atomic_response_and_schema_version.sql, "
            "20260824090000_calendar_watchlist_events.sql, and "
            "20260825090000_calendar_schema_gate.sql, and "
            "20260829090000_distinct_atomic_calendar_candidate_upsert_version.sql) "
            f"have not been applied to this Supabase project yet. Underlying error: {exc}",
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
        "Supabase schema gate passed: event_strategy_approvals, "
        "approve_strategy_draft(), and insert_next_expectation_version() are all "
        "present, insert_next_expectation_version() is on the required "
        "implementation version, calendar_events and calendar RPCs are present, "
        "and upsert_calendar_candidate() has the required placeholder-preserving "
        "implementation version."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
