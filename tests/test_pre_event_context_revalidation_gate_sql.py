from pathlib import Path
import unittest


class PreEventContextRevalidationGateSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902094000_pre_event_context_revalidation_gate.sql"
        ).read_text(encoding="utf-8")

    def test_function_is_security_invoker(self) -> None:
        self.assertIn(
            "create or replace function "
            "public.validate_tracked_market_event_pre_event_context_if_current(",
            self.sql,
        )
        self.assertIn("security invoker", self.sql)
        self.assertNotIn("security definer", self.sql)

    def test_execute_is_revoked_from_public_and_granted_only_to_service_role(self) -> None:
        self.assertIn(
            "revoke all on function "
            "public.validate_tracked_market_event_pre_event_context_if_current(\n"
            "  uuid, timestamptz\n"
            ") from public;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function "
            "public.validate_tracked_market_event_pre_event_context_if_current(\n"
            "  uuid, timestamptz\n"
            ") to service_role;",
            self.sql,
        )

    def test_row_lock_precedes_version_check_in_one_transaction(self) -> None:
        lock = self.sql.index("for update;")
        version_check = self.sql.index(
            "if existing_row.updated_at is distinct from input_expected_updated_at then"
        )
        conflict = self.sql.index("tracked_market_event_version_conflict")
        self.assertLess(lock, version_check)
        self.assertLess(version_check, conflict)
        self.assertIn("begin;", self.sql)
        self.assertIn("commit;", self.sql)

    def test_not_found_fails_closed_before_version_check(self) -> None:
        not_found = self.sql.index("tracked_market_event_not_found")
        version_check = self.sql.index(
            "if existing_row.updated_at is distinct from input_expected_updated_at then"
        )
        self.assertLess(not_found, version_check)


if __name__ == "__main__":
    unittest.main()
