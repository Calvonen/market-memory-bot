from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CANONICAL_CUTOVER = ROOT / "supabase/migrations/20260902111750_close_calendar_binding_compatibility_upsert.sql"
LEGACY_CUTOVER = ROOT / "supabase/migrations/20260902111800_close_legacy_tracked_event_calendar_binding_upsert.sql"
RELEASE_SHELL = ROOT / "supabase/migrations/20260902112000_canonical_tracked_event_release_shell.sql"


class CloseLegacyTrackedEventCalendarBindingUpsertMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = LEGACY_CUTOVER.read_text(encoding="utf-8")

    def test_runs_after_canonical_cutover_and_before_release_shell_v12(self) -> None:
        self.assertLess(CANONICAL_CUTOVER.name, LEGACY_CUTOVER.name)
        self.assertLess(LEGACY_CUTOVER.name, RELEASE_SHELL.name)

    def test_serializes_lower_level_cutover_with_tracked_event_writes(self) -> None:
        lock_sql = "lock table public.tracked_market_events in share row exclusive mode"
        rename_sql = "alter function public.upsert_tracked_market_event("
        self.assertIn(lock_sql, self.sql)
        self.assertIn(rename_sql, self.sql)
        self.assertLess(self.sql.index(lock_sql), self.sql.index(rename_sql))
        self.assertLess(self.sql.index(lock_sql), self.sql.index("select string_agg("))

    def test_runtime_writer_forbids_calendar_binding(self) -> None:
        self.assertIn("input_calendar_event_id uuid default null", self.sql)
        self.assertIn("raise exception 'tracked_market_event_calendar_binding_forbidden'", self.sql)
        self.assertIn("promote_calendar_event_to_tracked_runtime()", self.sql)

    def test_calendar_capable_legacy_body_is_owner_only(self) -> None:
        self.assertIn("rename to upsert_tracked_market_event_calendar_compat_v11", self.sql)
        self.assertIn(
            "from public, anon, authenticated, service_role",
            self.sql,
        )
        self.assertIn("security definer", self.sql)
        self.assertIn("set search_path = pg_catalog, public", self.sql)

    def test_wrapper_delegates_only_with_null_calendar_id(self) -> None:
        self.assertIn("input_actor,\n    null\n  );", self.sql)
        self.assertIn("grant execute on function public.upsert_tracked_market_event(", self.sql)

    def test_calendar_promotion_uses_private_calendar_capable_helper(self) -> None:
        promotion = self.sql.index(
            "create or replace function public.promote_calendar_event_to_tracked_runtime("
        )
        private_call = self.sql.index(
            "from public.upsert_tracked_market_event_calendar_compat_v11(",
            promotion,
        )
        release_shell = self.sql.index(
            "perform * from public.ensure_calendar_release_shell(calendar_row.id);",
            promotion,
        )
        self.assertLess(promotion, private_call)
        self.assertLess(private_call, self.sql.index("select string_agg("))
        self.assertIn("security definer", self.sql[promotion:private_call])
        self.assertIn("set search_path = pg_catalog, public", self.sql[promotion:private_call])
        self.assertIn("calendar_event_changed_before_promotion", self.sql[promotion:private_call])
        self.assertIn("calendar_runtime_binding_identity_conflict", self.sql[promotion:private_call])
        self.assertIn("'calendar:' || calendar_row.id::text", self.sql[promotion:private_call + 500])
        self.assertLess(release_shell, self.sql.index("commit;"))
        self.assertNotIn(
            "from public.upsert_tracked_market_event(\n    calendar_row.company_name",
            self.sql[promotion:],
        )

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
