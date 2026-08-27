from pathlib import Path
import unittest

MIGRATION = Path("supabase/migrations/20260902104000_official_release_source_approval_audit.sql")
SCHEMA_GATE = Path("scripts/verify_supabase_schema.py")


class OfficialReleaseSourceApprovalAuditSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text()
        cls.schema_gate = SCHEMA_GATE.read_text()

    def test_audit_is_append_only_service_readable(self):
        self.assertIn("create table public.event_official_release_source_audit", self.sql)
        self.assertIn("revoke all on table public.event_official_release_source_audit", self.sql)
        self.assertIn("grant select on table public.event_official_release_source_audit to service_role", self.sql)

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

    def test_old_unaudited_rpcs_are_revoked_from_service_role(self):
        self.assertIn("revoke all on function public.set_event_official_release_source(text, text, text, text, integer)\n  from service_role", self.sql)
        self.assertIn("revoke all on function public.clear_event_official_release_source(text, integer)\n  from service_role", self.sql)

    def test_approved_rpcs_require_actor_and_write_audit(self):
        self.assertIn("set_event_official_release_source_approved", self.sql)
        self.assertIn("clear_event_official_release_source_approved", self.sql)
        self.assertGreaterEqual(self.sql.count("input_actor text"), 2)
        self.assertGreaterEqual(self.sql.count("insert into public.event_official_release_source_audit"), 4)

    def test_schema_gate_has_distinct_audited_contract_version(self):
        self.assertIn("drop function public.verify_official_release_source_schema()", self.sql)
        self.assertIn("official_release_source_schema_version integer", self.sql)
        self.assertIn("event_official_release_source_audit", self.sql)
        self.assertIn("set_event_official_release_source_approved", self.sql)
        self.assertIn("clear_event_official_release_source_approved", self.sql)
        self.assertIn("REQUIRED_OFFICIAL_RELEASE_SOURCE_SCHEMA_VERSION = 2", self.schema_gate)
        self.assertIn(
            '"official_release_source_schema_version"',
            self.schema_gate,
        )
        self.assertIn("deployed_official_source_version", self.schema_gate)


if __name__ == "__main__":
    unittest.main()
