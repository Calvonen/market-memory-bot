from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
REPAIR = ROOT / "supabase/migrations/20260902111500_repair_legacy_tracked_calendar_binding.sql"
CUTOVER = ROOT / "supabase/migrations/20260902111750_close_calendar_binding_compatibility_upsert.sql"
RELEASE_SHELL = ROOT / "supabase/migrations/20260902112000_canonical_tracked_event_release_shell.sql"


class CloseCalendarBindingCompatibilityUpsertMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = CUTOVER.read_text(encoding="utf-8")

    def test_runs_after_legacy_repair_and_before_release_shell_v12(self) -> None:
        self.assertLess(REPAIR.name, CUTOVER.name)
        self.assertLess(CUTOVER.name, RELEASE_SHELL.name)

    def test_serializes_cutover_with_tracked_event_writes(self) -> None:
        lock_sql = "lock table public.tracked_market_events in share row exclusive mode"
        self.assertIn(lock_sql, self.sql)
        self.assertLess(self.sql.index(lock_sql), self.sql.index("alter function public.upsert_canonical_tracked_market_event("))
        self.assertLess(self.sql.index(lock_sql), self.sql.index("select string_agg("))

    def test_both_canonical_upsert_overloads_forbid_calendar_binding(self) -> None:
        self.assertEqual(
            self.sql.count("raise exception 'canonical_tracked_market_event_calendar_binding_forbidden'"),
            2,
        )
        self.assertIn("input_calendar_event_id uuid default null", self.sql)
        self.assertIn("input_expected_tracked_instrument_id text", self.sql)
        self.assertIn("promote_calendar_event_to_tracked_runtime()", self.sql)

    def test_old_compatibility_implementations_are_not_runtime_callable(self) -> None:
        self.assertIn("rename to upsert_canonical_tracked_market_event_calendar_compat_v11", self.sql)
        self.assertIn("rename to upsert_canonical_tracked_market_event_bound_calendar_compat_v11", self.sql)
        self.assertIn(
            "from public, anon, authenticated, service_role",
            self.sql,
        )
        self.assertIn("security definer", self.sql)
        self.assertIn("set search_path = pg_catalog, public", self.sql)

    def test_wrappers_delegate_only_with_null_calendar_id(self) -> None:
        self.assertIn("input_actor,\n    null\n  );", self.sql)
        self.assertIn("input_actor,\n    null,\n    input_expected_tracked_instrument_id", self.sql)

    def test_revalidates_binding_invariant_before_commit(self) -> None:
        scan = self.sql.index("select string_agg(")
        commit = self.sql.rindex("commit;")
        self.assertLess(scan, commit)
        self.assertIn("tracked_calendar_binding_invariant_conflicts", self.sql)
        self.assertIn("calendar_event_missing", self.sql)
        self.assertIn("instrument_mismatch", self.sql)
        self.assertIn("kind_mismatch", self.sql)
        self.assertIn("event_date_mismatch", self.sql)
        self.assertIn("source_mismatch", self.sql)
        self.assertIn("external_key_mismatch", self.sql)


if __name__ == "__main__":
    unittest.main()
