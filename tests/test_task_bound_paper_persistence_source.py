from __future__ import annotations

import unittest
from pathlib import Path


ORCHESTRATION = Path("trading_system/tracked_event_paper_orchestration.py")
REPOSITORY = Path("trading_system/paper_trade_repository.py")


class TaskBoundPaperPersistenceSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.orchestration = ORCHESTRATION.read_text(encoding="utf-8")
        cls.repository = REPOSITORY.read_text(encoding="utf-8")

    def test_orchestration_passes_requested_task_to_persistence(self) -> None:
        save_start = self.orchestration.index("persisted = paper_runs.save_result(")
        save_block = self.orchestration[save_start : save_start + 700]
        self.assertIn("task_id=requested_task_id", save_block)

    def test_repository_uses_task_aware_save_rpc_when_task_is_present(self) -> None:
        self.assertIn("task_id: str | None = None", self.repository)
        self.assertIn('"task_id": task_id', self.repository)
        self.assertIn('"save_event_paper_trade_result_for_task"', self.repository)
        self.assertIn('else "save_event_paper_trade_result"', self.repository)


if __name__ == "__main__":
    unittest.main()
