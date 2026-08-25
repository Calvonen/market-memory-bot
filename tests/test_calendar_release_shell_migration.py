from pathlib import Path
import unittest


MIGRATION = Path("supabase/migrations/20260902102000_calendar_release_shell.sql")


class CalendarReleaseShellMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MIGRATION.read_text(encoding="utf-8")
        cls.lower = cls.source.lower()

    def test_shell_uses_same_canonical_calendar_identity_as_runtime(self) -> None:
        self.assertIn("release_event_id := 'calendar:' || calendar_row.id::text", self.source)
        self.assertIn("where event_id = release_event_id", self.source)

    def test_shell_creates_market_event_from_locked_calendar_identity(self) -> None:
        calendar_lock = self.lower.index("from public.calendar_events")
        lock_clause = self.lower.index("for update", calendar_lock)
        market_insert = self.lower.index("insert into public.market_events", lock_clause)
        self.assertLess(lock_clause, market_insert)
        for field in (
            "calendar_row.instrument",
            "calendar_row.scheduled_date",
            "release_event_name",
        ):
            self.assertIn(field, self.source)

    def test_existing_market_event_identity_drift_fails_closed(self) -> None:
        self.assertIn("calendar_release_shell_identity_conflict", self.source)
        conflict = self.lower.index("calendar_release_shell_identity_conflict")
        expectation_insert = self.lower.index(
            "insert into public.event_expectation_versions", conflict
        )
        self.assertLess(conflict, expectation_insert)

    def test_expectation_shell_explicitly_infers_nothing(self) -> None:
        insert = self.lower.index("insert into public.event_expectation_versions")
        values = self.lower.index("values (", insert)
        end = self.lower.index(");", values)
        block = self.lower[values:end]
        self.assertIn("'{}'::jsonb", block)
        self.assertGreaterEqual(block.count("'[]'::jsonb"), 5)
        self.assertIn("no consensus or kpi expectations inferred", block)
        self.assertIn("source_url", self.lower[insert:values])
        self.assertIn("source_as_of", self.lower[insert:values])
        self.assertIn("null", block)

    def test_existing_expectation_versions_are_never_overwritten(self) -> None:
        exists = self.lower.index("select exists (")
        self.assertIn("from public.event_expectation_versions", self.lower[exists:])
        guarded_insert = self.lower.index("if not expectation_exists then", exists)
        insert = self.lower.index("insert into public.event_expectation_versions", guarded_insert)
        self.assertLess(guarded_insert, insert)
        shell_function_end = self.lower.index("end;\n$$;", insert)
        shell_body = self.lower[exists:shell_function_end]
        self.assertNotIn("update public.event_expectation_versions", shell_body)
        self.assertNotIn("delete from public.event_expectation_versions", shell_body)

    def test_release_shell_is_required_on_first_promotion_and_retry(self) -> None:
        promotion = self.lower.index(
            "create or replace function public.promote_calendar_event_to_tracked_runtime("
        )
        promotion_body = self.lower[promotion:]
        calls = promotion_body.count(
            "public.ensure_calendar_release_shell(calendar_row.id)"
        )
        self.assertEqual(calls, 2)
        noop = promotion_body.index("'noop_existing'::text")
        first_shell = promotion_body.index(
            "public.ensure_calendar_release_shell(calendar_row.id)"
        )
        self.assertLess(first_shell, noop)
        upsert = promotion_body.index("from public.upsert_tracked_market_event(")
        second_shell = promotion_body.index(
            "public.ensure_calendar_release_shell(calendar_row.id)", first_shell + 1
        )
        self.assertLess(upsert, second_shell)

    def test_v10_schema_gate_requires_release_shell_and_preserves_untrack_guard(self) -> None:
        self.assertIn("ensure_calendar_release_shell_function_exists boolean", self.source)
        self.assertIn("calendar_release_shell_version_matches boolean", self.source)
        self.assertIn("public.calendar_release_shell_version() = 1", self.source)
        self.assertIn("public.calendar_runtime_untrack_guard_version() = 1", self.source)
        self.assertIn("select 10;", self.source)

    def test_migration_is_one_transaction(self) -> None:
        stripped = "\n".join(
            line for line in self.source.splitlines() if not line.strip().startswith("--")
        ).strip().lower()
        self.assertTrue(stripped.startswith("begin;"))
        self.assertTrue(stripped.endswith("commit;"))


if __name__ == "__main__":
    unittest.main()
