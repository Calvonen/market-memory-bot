from __future__ import annotations

import unittest
from unittest.mock import Mock

from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.post_release_paper import PostReleasePaperResult


class EarningsObservationLifecyclePersistenceTests(unittest.TestCase):
    def test_repository_persists_observing_status_as_nonterminal_result(self) -> None:
        client = Mock()
        client.rpc.return_value.execute.return_value.data = [
            {
                "event_id": "tracked:event-1",
                "analysis_id": "analysis-1",
                "status": "observing_post_release",
                "message": "observing first 30 minutes after earnings",
            }
        ]
        repository = SupabasePaperTradeRepository(client)

        persisted = repository.save_result(
            event_id="tracked:event-1",
            expectation_version=3,
            source_document_id=None,
            analysis_id="analysis-1",
            result=PostReleasePaperResult(
                "observing_post_release",
                "observing first 30 minutes after earnings",
            ),
            task_id="task-1",
        )

        self.assertEqual(persisted["status"], "observing_post_release")
        rpc_name, rpc_args = client.rpc.call_args.args
        self.assertEqual(rpc_name, "save_event_paper_trade_result_for_task")
        payload = rpc_args["input_payload"]
        self.assertEqual(payload["status"], "observing_post_release")
        self.assertIsNone(payload["strategy"])
        self.assertIsNone(payload["risk"])
        self.assertIsNone(payload["paper_order"])


if __name__ == "__main__":
    unittest.main()
