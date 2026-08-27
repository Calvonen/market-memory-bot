from pathlib import Path
import unittest

MIGRATION = Path("supabase/migrations/20260902104000_official_release_source_approval_audit.sql")
INTEGRITY_MIGRATION = Path("supabase/migrations/20260902105000_official_release_source_audit_integrity.sql")
GENERATION_MIGRATION = Path("supabase/migrations/20260902106000_official_release_source_generation_integrity.sql")
TRIGGER_GATE_MIGRATION = Path("supabase/migrations/20260902107000_official_release_source_trigger_gate.sql")
SCHEMA_GATE = Path("scripts/verify_supabase_schema.py")


class OfficialReleaseSourceApprovalAuditSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text()
        cls.schema_gate = SCHEMA_GATE.read_text()
        cls.integrity_sql = INTEGRITY_MIGRATION.read_text()
        cls.generation_sql = GENERATION_MIGRATION.read_text()
        cls.trigger_gate_sql = TRIGGER_GATE_MIGRATION.read_text()

    def test_audit_is_append_only_service_readable(self):
        self.assertIn("create table public.event_official_release_source_audit", self.sql)
        self.assertIn("revoke all on table public.event_official_release_source_audit", self.sql)
        self.assertIn("grant select on table public.event_official_release_source_audit to service_role", self.sql)

    def test_audit_survives_parent_event_deletion(self):
        self.assertIn("event_id text not null,", self.sql)
        self.assertNotIn(
            "references public.market_events(event_id) on delete cascade",
            self.sql,
        )
        self.assertIn("retained independently of market_events lifecycle", self.sql)

    def test_pre_audit_active_sources_are_recorded_then_invalidated(self):
        self.assertIn("migration:legacy-pre-audit-source", self.sql)
        self.assertIn("migration:legacy-pre-audit-invalidation", self.sql)
        self.assertIn("from public.event_official_release_sources\nwhere is_active", self.sql)
        self.assertIn("update public.event_official_release_sources", self.sql)
        self.assertIn("source_kind = null", self.sql)
        self.assertIn("source_url = null", self.sql)
        self.assertIn("source_title = null", self.sql)
        self.assertIn("is_active = false", self.sql)
        self.assertIn("version = version + 1", self.sql)
        self.assertIn("from invalidated", self.sql)

        legacy_set = self.sql.index("migration:legacy-pre-audit-source")
        invalidation_update = self.sql.index("update public.event_official_release_sources")
        legacy_clear = self.sql.index("migration:legacy-pre-audit-invalidation")
        self.assertLess(legacy_set, invalidation_update)
        self.assertLess(invalidation_update, legacy_clear)

    def test_legacy_writers_are_revoked_committed_and_drained_before_scan(self):
        revoke_set = self.sql.index(
            "revoke all on function public.set_event_official_release_source(text, text, text, text, integer)"
        )
        revoke_clear = self.sql.index(
            "revoke all on function public.clear_event_official_release_source(text, integer)"
        )
        first_commit = self.sql.index("commit;", revoke_clear)
        second_begin = self.sql.index("begin;", first_commit)
        drain_lock = self.sql.index(
            "lock table public.event_official_release_sources in access exclusive mode",
            second_begin,
        )
        legacy_scan = self.sql.index("migration:legacy-pre-audit-source", drain_lock)

        self.assertLess(revoke_set, first_commit)
        self.assertLess(revoke_clear, first_commit)
        self.assertLess(first_commit, second_begin)
        self.assertLess(second_begin, drain_lock)
        self.assertLess(drain_lock, legacy_scan)

    def test_old_unaudited_rpcs_are_revoked_from_service_role(self):
        self.assertIn("revoke all on function public.set_event_official_release_source(text, text, text, text, integer)\n  from service_role", self.sql)
        self.assertIn("revoke all on function public.clear_event_official_release_source(text, integer)\n  from service_role", self.sql)

    def test_approved_rpcs_require_actor_and_write_audit(self):
        self.assertIn("set_event_official_release_source_approved", self.sql)
        self.assertIn("clear_event_official_release_source_approved", self.sql)
        self.assertGreaterEqual(self.sql.count("input_actor text"), 2)
        self.assertGreaterEqual(self.sql.count("insert into public.event_official_release_source_audit"), 4)

    def test_v3_freezes_event_identity_set_before_legacy_drain(self):
        event_lock = self.integrity_sql.index("lock table public.market_events in share mode")
        identity_snapshot = self.integrity_sql.index("select event_id from public.market_events")
        advisory_drain = self.integrity_sql.index(
            "pg_advisory_xact_lock(hashtextextended(item.event_id, 2))"
        )
        source_lock = self.integrity_sql.index(
            "lock table public.event_official_release_sources in access exclusive mode"
        )
        self.assertLess(event_lock, identity_snapshot)
        self.assertLess(event_lock, advisory_drain)
        self.assertLess(advisory_drain, source_lock)

    def test_v3_keeps_event_delete_blocked_until_fk_detached_and_trigger_installed(self):
        event_lock = self.integrity_sql.index("lock table public.market_events in share mode")
        fk_drop = self.integrity_sql.index(
            "drop constraint if exists event_official_release_sources_event_id_fkey"
        )
        trigger_create = self.integrity_sql.index(
            "create trigger tombstone_official_release_source_before_event_delete"
        )
        final_commit = self.integrity_sql.rindex("commit;")
        self.assertLess(event_lock, fk_drop)
        self.assertLess(fk_drop, trigger_create)
        self.assertLess(trigger_create, final_commit)

    def test_v3_drains_advisory_queues_and_rejects_unaudited_active_rows(self):
        self.assertIn("pg_advisory_xact_lock(hashtextextended(item.event_id, 2))", self.integrity_sql)
        self.assertIn("migration:post-revoke-unaudited-source", self.integrity_sql)
        self.assertIn("migration:post-revoke-unaudited-invalidation", self.integrity_sql)
        self.assertIn("get_audited_official_release_source_state", self.integrity_sql)
        self.assertIn("audit.version = source_row.version", self.integrity_sql)
        self.assertIn("source_row.is_active and audited_active", self.integrity_sql)

    def test_v3_preserves_source_generation_across_event_deletion(self):
        self.assertIn("drop constraint if exists event_official_release_sources_event_id_fkey", self.integrity_sql)
        self.assertIn("tombstone_official_release_source_before_event_delete", self.integrity_sql)
        self.assertIn("system:market-event-delete", self.integrity_sql)
        self.assertIn("before delete on public.market_events", self.integrity_sql)
        self.assertIn("    3;", self.integrity_sql)

    def test_v4_matches_only_latest_audit_generation(self):
        self.assertIn("select distinct on (event_id)", self.generation_sql)
        self.assertIn("order by event_id, id desc", self.generation_sql)
        self.assertIn("migration:stale-generation-source", self.generation_sql)
        self.assertIn("migration:stale-generation-invalidation", self.generation_sql)
        self.assertIn("order by id desc\n    limit 1", self.generation_sql)
        self.assertIn("latest_audit.action = 'set'", self.generation_sql)
        self.assertIn("latest_audit.version = source_row.version", self.generation_sql)

    def test_v4_clear_checks_live_parent_under_advisory_lock(self):
        clear_start = self.generation_sql.index(
            "create or replace function public.clear_event_official_release_source_approved"
        )
        lock_pos = self.generation_sql.index(
            "pg_advisory_xact_lock(hashtextextended(input_event_id, 2))",
            clear_start,
        )
        parent_check = self.generation_sql.index(
            "from public.market_events",
            lock_pos,
        )
        missing_error = self.generation_sql.index("event_not_found:%", parent_check)
        clear_call = self.generation_sql.index(
            "public.clear_event_official_release_source(",
            missing_error,
        )
        self.assertLess(lock_pos, parent_check)
        self.assertLess(parent_check, missing_error)
        self.assertLess(missing_error, clear_call)

    def test_v5_schema_gate_verifies_enabled_market_event_delete_trigger(self):
        self.assertIn("from pg_catalog.pg_trigger trigger_row", self.trigger_gate_sql)
        self.assertIn("relation_namespace.nspname = 'public'", self.trigger_gate_sql)
        self.assertIn("relation.relname = 'market_events'", self.trigger_gate_sql)
        self.assertIn(
            "trigger_row.tgname = 'tombstone_official_release_source_before_event_delete'",
            self.trigger_gate_sql,
        )
        self.assertIn("not trigger_row.tgisinternal", self.trigger_gate_sql)
        self.assertIn("trigger_row.tgtype = 11", self.trigger_gate_sql)
        self.assertIn("trigger_row.tgenabled in ('O', 'A')", self.trigger_gate_sql)
        self.assertIn(
            "trigger_function.proname = 'tombstone_official_release_source_before_event_delete'",
            self.trigger_gate_sql,
        )
        self.assertIn("    5;", self.trigger_gate_sql)

    def test_schema_gate_has_distinct_audited_contract_version(self):
        self.assertIn("drop function public.verify_official_release_source_schema()", self.sql)
        self.assertIn("official_release_source_schema_version integer", self.sql)
        self.assertIn("event_official_release_source_audit", self.sql)
        self.assertIn("set_event_official_release_source_approved", self.sql)
        self.assertIn("clear_event_official_release_source_approved", self.sql)
        self.assertIn("REQUIRED_OFFICIAL_RELEASE_SOURCE_SCHEMA_VERSION = 6", self.schema_gate)
        self.assertIn("    5;", self.trigger_gate_sql)
        self.assertIn(
            '"official_release_source_schema_version"',
            self.schema_gate,
        )
        self.assertIn("deployed_official_source_version", self.schema_gate)


if __name__ == "__main__":
    unittest.main()
