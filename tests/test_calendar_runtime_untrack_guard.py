from pathlib import Path
import unittest


MIGRATION = Path(
    "supabase/migrations/20260902101000_calendar_runtime_untrack_guard.sql"
)


class CalendarRuntimeUntrackGuardMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MIGRATION.read_text(encoding="utf-8")
        cls.lower = cls.source.lower()

    def test_calendar_row_is_locked_before_runtime_binding_guard(self) -> None:
        lock_index = self.lower.index("from public.calendar_events")
        lock_clause = self.lower.index("for update", lock_index)
        binding_guard = self.lower.index("from public.tracked_market_events", lock_clause)
        self.assertLess(lock_clause, binding_guard)

    def test_runtime_bound_row_cannot_transition_to_candidate(self) -> None:
        self.assertIn("input_to_status = 'candidate'", self.source)
        self.assertIn("where t.calendar_event_id = existing_row.id", self.source)
        self.assertIn("calendar_event_runtime_bound", self.source)
        guard_index = self.lower.index("calendar_event_runtime_bound")
        update_index = self.lower.index("update public.calendar_events", guard_index)
        self.assertLess(guard_index, update_index)

    def test_guard_runs_before_noop_already_candidate_response(self) -> None:
        guard_index = self.lower.index("calendar_event_runtime_bound")
        noop_index = self.lower.index("noop_already", guard_index)
        self.assertLess(guard_index, noop_index)

    def test_schema_gate_moves_to_runtime_version_9(self) -> None:
        self.assertIn("create or replace function public.calendar_runtime_untrack_guard_version()", self.source)
        self.assertIn("public.calendar_runtime_untrack_guard_version() = 1", self.source)
        self.assertIn("calendar_runtime_untrack_guard_version_matches boolean", self.source)
        self.assertIn("select 9;", self.source)

    def test_migration_is_one_transaction(self) -> None:
        stripped = "\n".join(
            line for line in self.source.splitlines() if not line.strip().startswith("--")
        ).strip().lower()
        self.assertTrue(stripped.startswith("begin;"))
        self.assertTrue(stripped.endswith("commit;"))


if __name__ == "__main__":
    unittest.main()
