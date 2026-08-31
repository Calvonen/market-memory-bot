from __future__ import annotations

import unittest
from pathlib import Path

from trading_system.tracked_event_paper_orchestration import _claim_is_owned


MIGRATION = Path(
    "supabase/migrations/20260903152000_paper_run_claim_version_identity.sql"
)
ORCHESTRATION = Path("trading_system/tracked_event_paper_orchestration.py")


class PaperClaimVersionIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()
        cls.orchestration = ORCHESTRATION.read_text(encoding="utf-8")

    def test_same_token_wrong_task_is_not_owned(self) -> None:
        claim = {
            "analysis_id": "analysis-1",
            "task_id": "task-a",
            "claim_token": "shared-token",
        }
        self.assertFalse(
            _claim_is_owned(
                claim,
                analysis_id="analysis-1",
                task_id="task-b",
                claim_token="shared-token",
            )
        )

    def test_orchestration_passes_expectation_version_to_claim(self) -> None:
        self.assertIn("expectation_version=expectation.version", self.orchestration)
        self.assertIn('"input_expectation_version": expectation_version', self.orchestration)
        self.assertIn("task_id=requested_task_id", self.orchestration)

    def test_claim_revalidates_current_expectation_version(self) -> None:
        self.assertIn("from public.current_event_expectations", self.sql)
        self.assertIn("current_expectation_version <> input_expectation_version", self.sql)
        self.assertIn("paper_run_expectation_version_changed", self.sql)

    def test_same_task_cannot_switch_analysis(self) -> None:
        self.assertIn("where task_id = input_task_id", self.sql)
        self.assertIn("existing_task_run.analysis_id <> input_analysis_id", self.sql)
        self.assertIn("paper_run_task_analysis_changed", self.sql)

    def test_legacy_five_argument_task_claim_is_disabled(self) -> None:
        self.assertIn(
            "revoke execute on function public.claim_event_paper_run_for_task(text, uuid, uuid, uuid, integer)",
            self.sql,
        )
        self.assertIn(
            "claim_event_paper_run_for_task(text, uuid, uuid, integer, uuid, integer)",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
