#!/usr/bin/env python3
"""Pre-deploy Supabase schema gate for MarketAI backend runtime dependencies.

The existing strategy-draft and calendar/watchlist checks stay in place. The
persistent tracked-event worker additionally requires the tracked-event runtime
migrations through the canonical tracked-event release-shell version. Manual
official release sources have a dedicated verifier RPC so a missing out-of-band
migration fails before backend/systemd processes are restarted.
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

REQUIRED_OFFICIAL_RELEASE_SOURCE_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "event_official_release_sources_table_exists",
        "event_official_release_sources table",
    ),
    (
        "set_event_official_release_source_function_exists",
        "set_event_official_release_source() function",
    ),
    (
        "clear_event_official_release_source_function_exists",
        "clear_event_official_release_source() function",
    ),
)

REQUIRED_TRACKED_EVENT_CHECKS: tuple[tuple[str, str], ...] = (
    ("tracked_market_events_table_exists", "tracked_market_events table"),
    (
        "tracked_market_event_reactions_table_exists",
        "tracked_market_event_reactions table",
    ),
    (
        "tracked_market_event_event_date_column_exists",
        "tracked_market_events.event_date date column",
    ),
    (
        "upsert_tracked_market_event_function_exists",
        "upsert_tracked_market_event() function",
    ),
    (
        "arm_tracked_market_event_resolution_function_exists",
        "arm_tracked_market_event_resolution() function",
    ),
    (
        "capture_tracked_market_event_reference_function_exists",
        "capture_tracked_market_event_reference() function",
    ),
    (
        "capture_tracked_market_event_reaction_anchor_function_exists",
        "capture_tracked_market_event_reaction_anchor() function",
    ),
    (
        "capture_tracked_market_event_config_snapshot_function_exists",
        "capture_tracked_market_event_config_snapshot() function",
    ),
    (
        "capture_tracked_market_event_pre_event_context_function_exists",
        "capture_tracked_market_event_pre_event_context() function",
    ),
    (
        "capture_tracked_market_event_pre_event_context_if_current_function_exists",
        "capture_tracked_market_event_pre_event_context_if_current() function",
    ),
    (
        "capture_tracked_market_event_pre_event_context_validated_function_exists",
        "capture_tracked_market_event_pre_event_context_validated() function",
    ),
    (
        "validate_tracked_market_event_pre_event_context_if_current_function_exists",
        "validate_tracked_market_event_pre_event_context_if_current() function",
    ),
    (
        "fail_tracked_market_event_pre_event_deadline_if_current_function_exists",
        "fail_tracked_market_event_pre_event_deadline_if_current() function",
    ),
    (
        "fail_tracked_market_event_stale_context_if_current_function_exists",
        "fail_tracked_market_event_stale_context_if_current() function",
    ),
    (
        "promote_calendar_event_to_tracked_runtime_function_exists",
        "promote_calendar_event_to_tracked_runtime() function",
    ),
    (
        "calendar_runtime_untrack_guard_version_matches",
        "calendar runtime-bound untrack guard implementation version",
    ),
    (
        "ensure_calendar_release_shell_function_exists",
        "ensure_calendar_release_shell() function",
    ),
    (
        "calendar_release_shell_version_matches",
        "calendar release-pipeline shell implementation version",
    ),
    (
        "ensure_tracked_event_release_shell_function_exists",
        "ensure_tracked_event_release_shell() function",
    ),
)

REQUIRED_CALENDAR_CANDIDATE_UPSERT_VERSION = 3
REQUIRED_OFFICIAL_RELEASE_SOURCE_SCHEMA_VERSION = 8
REQUIRED_TRACKED_EVENT_RUNTIME_SCHEMA_VERSION = 12
POSTGRES_IDENTIFIER_MAX_BYTES = 63


def _postgres_response_key(key_name: str) -> str:
    """Return the key PostgreSQL exposes for an unquoted result-column name."""
    encoded = key_name.encode("utf-8")
    if len(encoded) <= POSTGRES_IDENTIFIER_MAX_BYTES:
        return key_name
    return encoded[:POSTGRES_IDENTIFIER_MAX_BYTES].decode("utf-8", errors="ignore")


def _check_value(row: dict[str, Any], key_name: str) -> Any:
    """Read an exact verifier key, falling back only to PostgreSQL truncation."""
    if key_name in row:
        return row[key_name]
    truncated = _postgres_response_key(key_name)
    if truncated != key_name and truncated in row:
        return row[truncated]
    return None


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
    missing = [label for key_name, label in REQUIRED_CHECKS if not _check_value(row, key_name)]

    deployed_upsert_version = _check_value(row, "calendar_candidate_upsert_implementation_version")
    if deployed_upsert_version != REQUIRED_CALENDAR_CANDIDATE_UPSERT_VERSION:
        missing.append(
            "upsert_calendar_candidate() atomic implementation version "
            f"{REQUIRED_CALENDAR_CANDIDATE_UPSERT_VERSION} "
            f"(deployed: {deployed_upsert_version!r})"
        )

    try:
        official_source_response = client.rpc(
            "verify_official_release_source_schema", {}
        ).execute()
    except Exception as exc:
        print(
            "SCHEMA GATE FAILED: could not call verify_official_release_source_schema(). "
            "Apply the pending official-release-source migrations before deploying. "
            f"Underlying error: {exc}",
            file=sys.stderr,
        )
        return 1

    official_source_rows = getattr(official_source_response, "data", None) or []
    if not official_source_rows:
        print(
            "SCHEMA GATE FAILED: verify_official_release_source_schema() returned no rows.",
            file=sys.stderr,
        )
        return 1
    official_source_row: dict[str, Any] = official_source_rows[0]
    missing.extend(
        label
        for key_name, label in REQUIRED_OFFICIAL_RELEASE_SOURCE_CHECKS
        if not _check_value(official_source_row, key_name)
    )
    deployed_official_source_version = _check_value(
        official_source_row, "official_release_source_schema_version"
    )
    if deployed_official_source_version != REQUIRED_OFFICIAL_RELEASE_SOURCE_SCHEMA_VERSION:
        missing.append(
            "official-release-source schema version "
            f"{REQUIRED_OFFICIAL_RELEASE_SOURCE_SCHEMA_VERSION} "
            f"(deployed: {deployed_official_source_version!r})"
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
        label
        for key_name, label in REQUIRED_TRACKED_EVENT_CHECKS
        if not _check_value(tracked_row, key_name)
    )
    deployed_runtime_version = _check_value(tracked_row, "runtime_schema_version")
    if deployed_runtime_version != REQUIRED_TRACKED_EVENT_RUNTIME_SCHEMA_VERSION:
        missing.append(
            "tracked-event runtime schema version "
            f"{REQUIRED_TRACKED_EVENT_RUNTIME_SCHEMA_VERSION} "
            f"(deployed: {deployed_runtime_version!r})"
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
        "Supabase schema gate passed: strategy/calendar, official-release-source, "
        "and persistent tracked-event runtime dependencies are present."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
