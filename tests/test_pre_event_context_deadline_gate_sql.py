from pathlib import Path
import unittest


class PreEventContextDeadlineGateSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902093000_pre_event_context_deadline_gate.sql"
        ).read_text(encoding="utf-8")

    def test_exact_retry_is_checked_before_deadline(self) -> None:
        equality = self.sql.index(
            "if existing_row.pre_event_market_context = input_pre_event_market_context then"
        )
        deadline = self.sql.index(
            "if pg_catalog.clock_timestamp() >= existing_row.event_at then"
        )
        self.assertLess(equality, deadline)

    def test_new_context_is_rejected_after_event_at_before_version_or_capture(self) -> None:
        deadline = self.sql.index(
            "if pg_catalog.clock_timestamp() >= existing_row.event_at then"
        )
        version = self.sql.index(
            "if existing_row.updated_at is distinct from input_expected_updated_at then"
        )
        second_capture = self.sql.rindex(
            "from public.capture_tracked_market_event_pre_event_context("
        )
        self.assertLess(deadline, version)
        self.assertLess(deadline, second_capture)
        self.assertIn(
            "tracked_market_event_pre_event_context_deadline_passed",
            self.sql,
        )

    def test_canonical_capture_still_expands_composite_result(self) -> None:
        self.assertEqual(
            self.sql.count(
                "select * into saved_row\n    from public.capture_tracked_market_event_pre_event_context("
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
