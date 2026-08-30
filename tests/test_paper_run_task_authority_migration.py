from __future__ import annotations

import unittest
from pathlib import Path


MIGRATION = Path("supabase/migrations/20260903151000_paper_run_task_authority.sql")


class PaperRunTaskAuthorityMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_paper_run_and_claim_store_canonical_task_id(self) -> None:
        self.assertIn("alter table public.event_paper_trade_runs", self.sql)
        self.assertIn("add column if not exists task_id uuid", self.sql)
        self.assertIn("references public.trading_tasks(id) on delete restrict", self.sql)
        self.assertIn("event_paper_trade_runs_task_uidx", self.sql)

    def test_task_bound_claim_revalidates_execution_authority(self) -> None:
        self.assertIn("claim_event_paper_run_for_task", self.sql)
        self.assertIn("task_row.state <> 'approved'", self.sql)
        self.assertIn("task_row.mode <> 'paper'", self.sql)
        self.assertIn("task_row.source_event_id <> input_event_id", self.sql)
        self.assertIn("for share", self.sql)

    def test_terminal_owner_is_not_rebound_to_replacement_task(self) -> None:
        terminal_guard = self.sql.index("if claimed.terminal_status is not null then")
        task_update = self.sql.index("set task_id = input_task_id")
        self.assertLess(terminal_guard, task_update)

    def test_run_trigger_binds_claim_task_and_rejects_replacement(self) -> None:
        self.assertIn("bind_event_paper_run_task_from_claim", self.sql)
        self.assertIn("new.task_id := claimed_task_id", self.sql)
        self.assertIn("paper_run_task_replacement_conflict", self.sql)


if __name__ == "__main__":
    unittest.main()
