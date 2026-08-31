from __future__ import annotations

import unittest
from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20260903153000_task_approval_analysis_lineage.sql"
)


class TaskApprovalAnalysisLineageMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_approval_persists_current_expectation_lineage_under_event_lock(self) -> None:
        self.assertIn("approved_expectation_version integer", self.sql)
        self.assertIn("pg_advisory_xact_lock(hashtextextended(source_event, 0))", self.sql)
        self.assertIn("from public.current_event_expectations", self.sql)
        self.assertIn("approved_expectation_version = current_version", self.sql)

    def test_claim_requires_approved_version_and_single_exact_analysis(self) -> None:
        self.assertIn("task_row.approved_expectation_version", self.sql)
        self.assertIn("paper_run_task_expectation_lineage_changed", self.sql)
        self.assertIn("from public.event_ai_analyses", self.sql)
        self.assertIn("analysis_count <> 1", self.sql)
        self.assertIn("canonical_analysis_id <> input_analysis_id", self.sql)
        self.assertIn("paper_run_analysis_lineage_ambiguous", self.sql)

    def test_analysis_writes_share_event_lineage_lock(self) -> None:
        self.assertIn("lock_event_ai_analysis_lineage", self.sql)
        self.assertIn("before insert or update on public.event_ai_analyses", self.sql)
        self.assertIn("pg_advisory_xact_lock(hashtextextended(new.event_id, 0))", self.sql)

    def test_task_aware_save_requires_exact_task_analysis_and_token(self) -> None:
        self.assertIn("save_event_paper_trade_result_for_task", self.sql)
        self.assertIn("claim_row.task_id <> effective_task_id", self.sql)
        self.assertIn("claim_row.analysis_id <> effective_analysis_id", self.sql)
        self.assertIn("claim_row.claim_token <> effective_claim_token", self.sql)
        self.assertIn("task_row.state <> 'approved'", self.sql)
        self.assertIn("task_row.mode <> 'paper'", self.sql)


if __name__ == "__main__":
    unittest.main()
