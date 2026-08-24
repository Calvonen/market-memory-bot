from pathlib import Path
import unittest


class PreEventContextVersionGateMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902092000_pre_event_context_version_gate.sql"
        ).read_text(encoding="utf-8")

    def test_exact_retry_is_checked_before_version_conflict(self) -> None:
        equality_check = (
            "if existing_row.pre_event_market_context = input_pre_event_market_context then"
        )
        version_check = (
            "if existing_row.updated_at is distinct from input_expected_updated_at then"
        )

        self.assertIn(equality_check, self.sql)
        self.assertIn(version_check, self.sql)
        self.assertLess(self.sql.index(equality_check), self.sql.index(version_check))

    def test_exact_retry_still_delegates_to_canonical_capture_validation(self) -> None:
        equality_start = self.sql.index(
            "if existing_row.pre_event_market_context = input_pre_event_market_context then"
        )
        version_start = self.sql.index(
            "if existing_row.updated_at is distinct from input_expected_updated_at then"
        )
        equality_branch = self.sql[equality_start:version_start]

        self.assertIn(
            "public.capture_tracked_market_event_pre_event_context(",
            equality_branch,
        )


if __name__ == "__main__":
    unittest.main()
