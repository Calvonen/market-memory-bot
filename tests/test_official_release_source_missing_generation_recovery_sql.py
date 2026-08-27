from pathlib import Path
import unittest

MIGRATION = Path("supabase/migrations/20260902108000_official_release_source_missing_generation_recovery.sql")
SCHEMA_GATE = Path("scripts/verify_supabase_schema.py")


class OfficialReleaseSourceMissingGenerationRecoverySqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text()
        cls.schema_gate = SCHEMA_GATE.read_text()

    def test_missing_generation_is_reconstructed_from_latest_audit(self):
        self.assertIn("select distinct on (audit.event_id)", self.sql)
        self.assertIn("order by audit.event_id, audit.id desc", self.sql)
        self.assertIn("where not exists (", self.sql)
        self.assertIn("missing.action = 'clear'", self.sql)
        self.assertIn("missing.action = 'set'", self.sql)
        self.assertIn("missing.version + 1", self.sql)
        self.assertIn("migration:pre-durable-delete-recovery", self.sql)

    def test_recovery_runs_under_event_and_source_locks(self):
        event_lock = self.sql.index("lock table public.market_events in share mode")
        source_lock = self.sql.index("lock table public.event_official_release_sources in access exclusive mode")
        snapshot = self.sql.index("select distinct on (audit.event_id)")
        self.assertLess(event_lock, source_lock)
        self.assertLess(source_lock, snapshot)

    def test_schema_gate_requires_recovery_contract_v6(self):
        self.assertIn("    6;", self.sql)
        self.assertIn("REQUIRED_OFFICIAL_RELEASE_SOURCE_SCHEMA_VERSION = 6", self.schema_gate)


if __name__ == "__main__":
    unittest.main()
