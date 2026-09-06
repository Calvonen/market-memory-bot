from __future__ import annotations

import unittest

from trading_system.assistant_event_proposal import AssistantEventProposalRecord
from trading_system.assistant_proposal_materializer import (
    AssistantProposalMaterializer,
    AssistantProposalReviewedVersionConflict,
)


PROPOSAL_ID = "00000000-0000-0000-0000-000000000327"


class FakeProposals:
    def get(self, proposal_id: str):
        if proposal_id != PROPOSAL_ID:
            return None
        return AssistantEventProposalRecord(
            id=PROPOSAL_ID,
            proposal_key="SYR.ASX:earnings:2026-09-07",
            payload={},
            requested_execution_mode="demo",
            status="approved_for_materialization",
            reviewed_by="marko",
            review_round=4,
        )


class AssistantProposalMaterializerRoundBindingTests(unittest.TestCase):
    def test_initial_read_rejects_intervening_review_round_before_any_other_work(self) -> None:
        materializer = AssistantProposalMaterializer(
            proposals=FakeProposals(),
            resolver=object(),
            registry=object(),
            events=object(),
            release_targets=object(),
            expectations=object(),
            official_sources=object(),
            approvals=object(),
        )

        with self.assertRaises(AssistantProposalReviewedVersionConflict):
            materializer.materialize(PROPOSAL_ID, expected_review_round=3)


if __name__ == "__main__":
    unittest.main()
