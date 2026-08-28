from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260902111500_repair_legacy_tracked_calendar_binding.sql"
NEXT_MIGRATION = ROOT / "supabase/migrations/20260902112000_canonical_tracked_event_release_shell.sql"


class RepairLegacyTrackedCalendarBindingMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_runs_before_release_shell_backfill(self) -> None:
        self.assertLess(MIGRATION.name, NEXT_MIGRATION.name)

    def test_repairs_only_unambiguous_terminal_non_calendar_identity(self) -> None:
        self.assertIn("set calendar_event_id = null", self.sql)
        self.assertIn("t.status in ('completed', 'failed', 'cancelled')", self.sql)
        self.assertIn("upper(replace(c.instrument, ' ', '')) = t.instrument", self.sql)
        self.assertIn("c.event_type = t.kind", self.sql)
        self.assertIn("c.scheduled_date = t.event_date", self.sql)
        self.assertIn("c.source is distinct from t.source", self.sql)
        self.assertIn("lower(btrim(t.external_key)) not like 'calendar:%'", self.sql)

    def test_malformed_calendar_keys_are_not_detached(self) -> None:
        self.assertIn("lower(btrim(t.external_key)) not like 'calendar:%'", self.sql)
        self.assertIn("lower(btrim(tracked_row.external_key)) like 'calendar:%'", self.sql)
        self.assertIn("malformed calendar", self.sql)
        self.assertIn("external_key_mismatch", self.sql)

    def test_serializes_with_release_shell_lock_order_and_rechecks_after_locks(self) -> None:
        calendar_lock = self.sql.index(
            "from public.calendar_events\n    where id = candidate.calendar_event_id\n    for update;"
        )
        tracked_lock = self.sql.index(
            "from public.tracked_market_events\n    where id = candidate.id\n    for update;"
        )
        dependency_check = self.sql.index(
            "if exists (select 1 from public.market_events where event_id = old_release_event_id)"
        )
        audit_insert = self.sql.index(
            "insert into public.legacy_tracked_calendar_binding_repairs"
        )
        quarantine = self.sql.index(
            "update public.calendar_events\n      set status = 'research'"
        )
        detach = self.sql.index(
            "update public.tracked_market_events\n    set calendar_event_id = null"
        )
        self.assertLess(calendar_lock, tracked_lock)
        self.assertLess(tracked_lock, dependency_check)
        self.assertLess(dependency_check, audit_insert)
        self.assertLess(audit_insert, quarantine)
        self.assertLess(quarantine, detach)
        self.assertIn("tracked_row.calendar_event_id is distinct from candidate.calendar_event_id", self.sql)
        self.assertIn("Recheck the complete safe-detach predicate after both locks are held", self.sql)

    def test_quarantine_is_audited_before_detach(self) -> None:
        self.assertIn("create table if not exists public.legacy_tracked_calendar_binding_repairs", self.sql)
        self.assertIn("calendar_event_id uuid not null", self.sql)
        self.assertIn("tracked_event_id uuid not null", self.sql)
        self.assertIn("prior_calendar_status text not null", self.sql)
        self.assertIn("prior_calendar_source text not null", self.sql)
        self.assertIn("tracked_source text not null", self.sql)
        self.assertIn("tracked_external_key text not null", self.sql)
        self.assertIn("repair_action text not null", self.sql)
        self.assertIn("repair_reason text not null", self.sql)
        self.assertIn("'quarantine_and_detach'", self.sql)
        self.assertIn("migration-only lifecycle repair", self.sql)
        self.assertIn("audit row above records the prior state", self.sql)

    def test_repair_audit_table_is_operator_only_and_immutable_over_data_api(self) -> None:
        self.assertIn(
            "alter table public.legacy_tracked_calendar_binding_repairs enable row level security",
            self.sql,
        )
        self.assertIn(
            "revoke all on table public.legacy_tracked_calendar_binding_repairs from public, anon, authenticated",
            self.sql,
        )
        self.assertIn(
            "grant select on table public.legacy_tracked_calendar_binding_repairs to service_role",
            self.sql,
        )
        self.assertIn(
            "revoke all on sequence public.legacy_tracked_calendar_binding_repairs_id_seq from public, anon, authenticated, service_role",
            self.sql,
        )
        self.assertNotIn(
            "grant insert on table public.legacy_tracked_calendar_binding_repairs to service_role",
            self.sql,
        )
        self.assertNotIn(
            "grant update on table public.legacy_tracked_calendar_binding_repairs to service_role",
            self.sql,
        )
        self.assertNotIn(
            "grant delete on table public.legacy_tracked_calendar_binding_repairs to service_role",
            self.sql,
        )

    def test_quarantines_trackable_calendar_row_before_detach(self) -> None:
        self.assertIn("calendar_row.status in ('candidate', 'tracked')", self.sql)
        self.assertIn("set status = 'research'", self.sql)
        self.assertIn("status = calendar_row.status", self.sql)
        self.assertIn("detached calendar row must not remain trackable", self.sql)

    def test_preserves_all_dependent_release_and_trading_state(self) -> None:
        for table in (
            "market_events",
            "event_expectation_versions",
            "event_official_release_sources",
            "event_official_release_source_audit",
            "event_source_documents",
            "event_ai_analyses",
            "event_ingestion_runs",
            "event_strategy_approvals",
            "event_paper_trade_event_claims",
            "event_paper_trade_runs",
        ):
            self.assertIn(f"from public.{table} where event_id = old_release_event_id", self.sql)

    def test_remaining_conflicts_abort_with_ids_and_reasons(self) -> None:
        self.assertIn("string_agg(", self.sql)
        self.assertIn("calendar_event_missing", self.sql)
        self.assertIn("instrument_mismatch", self.sql)
        self.assertIn("kind_mismatch", self.sql)
        self.assertIn("event_date_mismatch", self.sql)
        self.assertIn("source_mismatch", self.sql)
        self.assertIn("external_key_mismatch", self.sql)
        self.assertIn("tracked_release_shell_legacy_binding_conflicts", self.sql)
        self.assertIn("raise exception", self.sql)


if __name__ == "__main__":
    unittest.main()
