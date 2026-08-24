from pathlib import Path
import re
import unittest


class PreEventContextVersionGateSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902092000_pre_event_context_version_gate.sql"
        ).read_text(encoding="utf-8")

    def test_canonical_capture_composite_is_expanded_into_rowtype(self) -> None:
        expanded_call = re.compile(
            r"select\s+\*\s+into\s+saved_row\s+"
            r"from\s+public\.capture_tracked_market_event_pre_event_context\s*\(",
            re.IGNORECASE | re.DOTALL,
        )
        self.assertEqual(len(expanded_call.findall(self.sql)), 2)
        self.assertNotRegex(
            self.sql,
            re.compile(
                r"select\s+public\.capture_tracked_market_event_pre_event_context\s*\(.*?\)\s*"
                r"into\s+saved_row",
                re.IGNORECASE | re.DOTALL,
            ),
        )

    def test_exact_retry_check_remains_before_version_conflict(self) -> None:
        equal_pos = self.sql.index(
            "if existing_row.pre_event_market_context = input_pre_event_market_context then"
        )
        conflict_pos = self.sql.index(
            "if existing_row.updated_at is distinct from input_expected_updated_at then"
        )
        self.assertLess(equal_pos, conflict_pos)


if __name__ == "__main__":
    unittest.main()
