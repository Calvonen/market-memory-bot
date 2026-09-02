from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260903161000_trading_task_position_cap_and_expected_approval.sql"
)


class PaperPermissionMigrationGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_approval_uses_canonical_lineage_then_execution_lock_order(self) -> None:
        lineage = self.sql.index("hashtextextended(canonical_source_event_id, 1)")
        execution = self.sql.index("hashtextextended(canonical_source_event_id, 0)")
        expectation_read = self.sql.index("select version into current_version")
        self.assertLess(lineage, execution)
        self.assertLess(execution, expectation_read)

    def test_task_replacement_reuses_canonical_cancel_boundary(self) -> None:
        self.assertIn(
            "perform public.cancel_trading_task(active_task.id, actor);",
            self.sql,
        )
        replacement_section = self.sql.split(
            "-- A changed cap, stale lineage, or pending predecessor is historical intent,",
            1,
        )[1].split("insert into public.trading_tasks", 1)[0]
        self.assertNotIn("set\n      state = 'cancelled'", replacement_section)


if __name__ == "__main__":
    unittest.main()
