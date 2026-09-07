from __future__ import annotations

import unittest

from trading_system.assistant_event_proposal import ExistingProposalApproval
from trading_system.assistant_proposal_materializer import (
    AssistantProposalCanonicalIdentityConflict,
    AssistantProposalMaterializer,
)


class AssistantReceiptRecoveryTests(unittest.TestCase):
    def test_db_derived_fingerprint_is_not_compared_to_legacy_caller_hash(self) -> None:
        receipt = ExistingProposalApproval(
            event_id="tracked:abc",
            expectation_version=2,
            draft_fingerprint="a" * 64,
        )

        self.assertEqual(
            AssistantProposalMaterializer._matching_receipt_version(
                receipt,
                "tracked:abc",
                2,
            ),
            2,
        )

    def test_recovery_rejects_wrong_event(self) -> None:
        receipt = ExistingProposalApproval(
            event_id="tracked:other",
            expectation_version=2,
            draft_fingerprint="a" * 64,
        )
        with self.assertRaises(AssistantProposalCanonicalIdentityConflict):
            AssistantProposalMaterializer._matching_receipt_version(
                receipt,
                "tracked:abc",
                2,
            )

    def test_recovery_rejects_wrong_version(self) -> None:
        receipt = ExistingProposalApproval(
            event_id="tracked:abc",
            expectation_version=3,
            draft_fingerprint="a" * 64,
        )
        with self.assertRaises(AssistantProposalCanonicalIdentityConflict):
            AssistantProposalMaterializer._matching_receipt_version(
                receipt,
                "tracked:abc",
                2,
            )

    def test_recovery_rejects_non_db_fingerprint_shape(self) -> None:
        receipt = ExistingProposalApproval(
            event_id="tracked:abc",
            expectation_version=2,
            draft_fingerprint="caller-controlled",
        )
        with self.assertRaises(AssistantProposalCanonicalIdentityConflict):
            AssistantProposalMaterializer._matching_receipt_version(
                receipt,
                "tracked:abc",
                2,
            )


if __name__ == "__main__":
    unittest.main()
