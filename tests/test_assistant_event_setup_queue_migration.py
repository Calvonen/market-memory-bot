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


class AssistantEventSetupQueueMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_queue_is_backend_only_and_cannot_grant_paper_authority(self) -> None:
        self.assertIn(
            "alter table public.assistant_event_setup_requests enable row level security;",
            self.sql,
        )
        self.assertIn(
            "revoke all on table public.assistant_event_setup_requests from anon, authenticated;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.apply_assistant_event_setup_request(uuid, text) to service_role;",
            self.sql,
        )
        self.assertNotIn("trading_tasks", self.sql)
        self.assertNotIn("approve_trading", self.sql)
        self.assertNotIn("paper_broker", self.sql)

    def test_materialization_reuses_canonical_control_surfaces(self) -> None:
        self.assertIn("public.upsert_tracked_market_event(", self.sql)
        self.assertIn("public.ensure_tracked_event_release_shell_with_blocker(tracked_id)", self.sql)
        self.assertIn("public.get_audited_official_release_source_state(release_id)", self.sql)
        self.assertIn("public.set_event_official_release_source_approved(", self.sql)
        self.assertIn("public.insert_next_expectation_version(", self.sql)

    def test_completed_request_is_idempotent(self) -> None:
        self.assertIn("if req.status = 'completed' then", self.sql)
        self.assertIn("perform pg_advisory_xact_lock", self.sql)
        self.assertIn("request_key text not null unique", self.sql)
        self.assertIn("'assistant:' || req.request_key", self.sql)

    def test_existing_different_official_source_fails_closed(self) -> None:
        self.assertIn("if existing_source_active then", self.sql)
        self.assertIn("assistant_setup_official_source_conflict", self.sql)

    def test_runtime_anchor_must_match_occurrence_date(self) -> None:
        self.assertIn(
            "if (req.event_at at time zone 'UTC')::date <> req.scheduled_date then",
            self.sql,
        )
        self.assertIn("assistant_setup_event_at_date_mismatch", self.sql)

    def test_strategy_objects_reject_nested_non_scalar_values(self) -> None:
        self.assertIn("from jsonb_each(req.strategy_payload->'consensus')", self.sql)
        self.assertIn("not in ('string', 'number', 'null')", self.sql)
        self.assertIn("assistant_setup_consensus_value_invalid", self.sql)
        self.assertIn("from jsonb_each(req.strategy_payload->'triggers')", self.sql)
        self.assertIn("not in ('string', 'number')", self.sql)
        self.assertIn("assistant_setup_trigger_value_invalid", self.sql)

    def test_strategy_lists_reject_non_string_elements(self) -> None:
        for field in (
            "important_kpis",
            "bull_case",
            "base_case",
            "bear_case",
            "invalidation_conditions",
        ):
            self.assertIn(
                f"from jsonb_array_elements(req.strategy_payload->'{field}')",
                self.sql,
            )
            self.assertIn(f"assistant_setup_{field}_value_invalid", self.sql)

    def test_strategy_source_metadata_requires_string_or_null(self) -> None:
        for field in ("source_name", "source_url", "source_as_of"):
            self.assertIn(
                f"jsonb_typeof(req.strategy_payload->'{field}') not in ('string', 'null')",
                self.sql,
            )
            self.assertIn(f"assistant_setup_{field}_invalid", self.sql)


if __name__ == "__main__":
    unittest.main()
