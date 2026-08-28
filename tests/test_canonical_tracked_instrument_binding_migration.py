from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260902111000_canonical_tracked_instrument_binding.sql"


class CanonicalTrackedInstrumentBindingMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_rpc_accepts_expected_tracked_instrument_identity(self) -> None:
        self.assertIn("input_expected_tracked_instrument_id text", self.sql)
        self.assertIn("tracked_market_event_instrument_binding_conflict", self.sql)

    def test_fresh_insert_is_rebound_before_transaction_commit(self) -> None:
        self.assertIn("if upserted.out_action = 'inserted' then", self.sql)
        self.assertIn(
            "set tracked_instrument_id = input_expected_tracked_instrument_id",
            self.sql,
        )
        self.assertLess(
            self.sql.index("set tracked_instrument_id = input_expected_tracked_instrument_id"),
            self.sql.rindex("commit;"),
        )

    def test_existing_identity_conflict_fails_inside_rpc(self) -> None:
        conflict = (
            "elsif saved_row.tracked_instrument_id is distinct from "
            "input_expected_tracked_instrument_id then"
        )
        self.assertIn(conflict, self.sql)
        self.assertIn("raise exception 'tracked_market_event_instrument_binding_conflict'", self.sql)

    def test_new_overload_is_service_role_only(self) -> None:
        signature = (
            "text, text, text, text, text, text, text, timestamptz, date, "
            "text, text, uuid, text"
        )
        self.assertIn(signature, self.sql)
        self.assertIn("to service_role", self.sql)


if __name__ == "__main__":
    unittest.main()
