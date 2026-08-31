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

    def test_legacy_claim_cannot_reclaim_task_bound_authority(self) -> None:
        self.assertIn("paper_run_task_bound_claim_requires_task", self.sql)
        self.assertIn("existing_claim.task_id is not null", self.sql)

    def test_terminal_owner_is_not_rebound_to_replacement_task(self) -> None:
        self.assertIn("status in ('expired_no_trade', 'paper_executed')", self.sql)
        self.assertIn("terminal_run.task_id", self.sql)
        self.assertIn("old.status in ('expired_no_trade', 'paper_executed')", self.sql)
        self.assertIn("paper_run_task_replacement_conflict", self.sql)

    def test_cancelled_task_can_be_replaced_after_lease_expiry(self) -> None:
        self.assertIn("existing_claim.lease_expires_at > now_value", self.sql)
        self.assertIn("old_task_row.state <> 'cancelled'", self.sql)
        self.assertIn("task_id = input_task_id", self.sql)
        self.assertIn("previous_task.state <> 'cancelled'", self.sql)

    def test_run_trigger_revalidates_current_task_before_persistence(self) -> None:
        self.assertIn("bind_event_paper_run_task_from_claim", self.sql)
        self.assertIn("claimed_task.state <> 'approved'", self.sql)
        self.assertIn("claimed_task.mode <> 'paper'", self.sql)
        self.assertIn("paper_run_task_authority_revoked", self.sql)
        self.assertIn("new.task_id := claimed_task_id", self.sql)


if __name__ == "__main__":
    unittest.main()
