#!/usr/bin/env python3
"""Fail closed unless the market-open PAPER runtime schema is fully deployed."""

from __future__ import annotations

import os
import sys


def main() -> int:
    url = os.environ.get("MARKETAI_SUPABASE_URL")
    key = os.environ.get("MARKETAI_SUPABASE_SECRET_KEY")
    if not url or not key:
        print(
            "MARKET-OPEN SCHEMA GATE FAILED: Supabase credentials are missing.",
            file=sys.stderr,
        )
        return 1

    try:
        from supabase import create_client

        response = create_client(url, key).rpc("verify_market_open_runtime_schema", {}).execute()
    except Exception as exc:
        print(
            "MARKET-OPEN SCHEMA GATE FAILED: verify_market_open_runtime_schema() is unavailable. "
            f"Apply the market-open migration before activating the dispatcher. Error: {exc}",
            file=sys.stderr,
        )
        return 1

    rows = getattr(response, "data", None) or []
    if len(rows) != 1 or not isinstance(rows[0], dict):
        print(
            "MARKET-OPEN SCHEMA GATE FAILED: verifier returned invalid data.",
            file=sys.stderr,
        )
        return 1

    row = rows[0]
    required = {
        "market_open_shell_function_exists": "ensure_market_open_strategy_shell(uuid)",
        "market_open_shell_trigger_exists": "tracked_market_events_market_open_shell_after_date_write trigger",
        "freeze_market_open_evidence_function_exists": (
            "freeze_market_open_evidence(uuid,integer,text,jsonb) and "
            "recover_completed_event_paper_broker_attempt_for_task(text,uuid)"
        ),
    }
    missing = [label for key_name, label in required.items() if row.get(key_name) is not True]
    if missing:
        print(
            "MARKET-OPEN SCHEMA GATE FAILED: missing " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    print("Market-open runtime schema gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
