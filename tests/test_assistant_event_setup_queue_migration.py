from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260906080000_assistant_event_setup_queue.sql"
)


class AssistantEventProposalStorageMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_storage_is_backend_only(self) -> None:
        self.assertIn(
            "alter table public.assistant_event_proposals enable row level security;",
            self.sql,
        )
        self.assertIn(
            "revoke all on table public.assistant_event_proposals from anon, authenticated;",
            self.sql,
        )
        self.assertIn(
            "grant select, insert, update on table public.assistant_event_proposals to service_role;",
            self.sql,
        )

    def test_storage_has_no_materialization_or_execution_authority(self) -> None:
        for forbidden in (
            "upsert_tracked_market_event",
            "upsert_canonical_tracked_market_event",
            "ensure_tracked_event_release_shell_with_blocker",
            "insert_next_expectation_version",
            "set_event_official_release_source_approved",
            "trading_tasks",
            "paper_broker",
        ):
            self.assertNotIn(forbidden, self.sql)

    def test_payload_is_opaque_json_for_backend_validation(self) -> None:
        self.assertIn("payload jsonb not null", self.sql)
        self.assertIn("check (jsonb_typeof(payload) = 'object')", self.sql)
        self.assertNotIn("strategy_payload->", self.sql)

    def test_demo_and_live_are_modeled_without_enabling_live(self) -> None:
        self.assertIn("requested_execution_mode text not null default 'demo'", self.sql)
        self.assertIn("check (requested_execution_mode in ('demo', 'live'))", self.sql)
        self.assertNotIn("live_enabled", self.sql)
        self.assertNotIn("execute_live", self.sql)

    def test_review_lifecycle_is_explicit(self) -> None:
        for status in (
            "draft",
            "ready_for_review",
            "rejected",
            "approved_for_materialization",
            "materialized",
        ):
            self.assertIn(f"'{status}'", self.sql)
        self.assertIn("proposal_key text not null unique", self.sql)
        self.assertIn("reviewed_by text", self.sql)
        self.assertIn("reviewed_at timestamptz", self.sql)


if __name__ == "__main__":
    unittest.main()
