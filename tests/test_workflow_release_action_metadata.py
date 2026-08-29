from __future__ import annotations

import unittest

from trading_system.workflow_readiness_evidence_loader import _release_action_metadata


class WorkflowReleaseActionMetadataTests(unittest.TestCase):
    def test_persisted_tracked_blocker_preserves_canonical_code_and_reason(self):
        code, reason = _release_action_metadata(
            None,
            {
                "blocker_code": "tracked_release_calendar_binding_identity_conflict",
                "message": "calendar binding conflicts with tracked-event identity",
            },
            release_document_present=False,
        )

        self.assertEqual(code, "tracked_release_calendar_binding_identity_conflict")
        self.assertEqual(reason, "calendar binding conflicts with tracked-event identity")

    def test_canonical_worker_blocker_strips_transport_prefix(self):
        code, reason = _release_action_metadata(
            {
                "provider": "canonical_release_worker",
                "status": "error",
                "error_message": "action_required: approved official source is missing",
            },
            None,
            release_document_present=False,
        )

        self.assertEqual(code, "release_action_required")
        self.assertEqual(reason, "approved official source is missing")

    def test_generic_error_without_document_gets_stable_code(self):
        code, reason = _release_action_metadata(
            {
                "provider": "results_page_official_release",
                "status": "error",
                "error_message": "provider failed before document persistence",
            },
            None,
            release_document_present=False,
        )

        self.assertEqual(code, "release_ingestion_error")
        self.assertEqual(reason, "provider failed before document persistence")

    def test_overdue_no_release_gets_stable_code(self):
        message = "release overdue: scheduled_date=2026-08-28 still no_release"
        code, reason = _release_action_metadata(
            {
                "provider": "results_page_official_release",
                "status": "no_release",
                "error_message": message,
            },
            None,
            release_document_present=False,
        )

        self.assertEqual(code, "release_overdue")
        self.assertEqual(reason, message)

    def test_existing_document_suppresses_noncanonical_ingestion_error(self):
        self.assertEqual(
            _release_action_metadata(
                {
                    "provider": "results_page_official_release",
                    "status": "error",
                    "error_message": "downstream analysis failed",
                },
                None,
                release_document_present=True,
            ),
            (None, None),
        )


if __name__ == "__main__":
    unittest.main()
