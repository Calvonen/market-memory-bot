from __future__ import annotations

import unittest

from pydantic import ValidationError

from trading_system.assistant_event_proposal import (
    AssistantEventProposalRecord,
    AssistantProposalIdentityConflict,
    AssistantProposalLiveLocked,
    AssistantProposalNotReady,
    proposal_approval_via,
    validate_proposal_for_materialization,
)


def _payload() -> dict:
    return {
        "company_name": " Syrah Resources Limited ",
        "instrument": " SYR.ASX ",
        "market": " Australia ",
        "kind": "earnings",
        "title": " Syrah Resources results ",
        "scheduled_date": "2026-09-07",
        "event_at": "2026-09-07T00:00:00+00:00",
        "event_time_status": "estimated",
        "official_source": {
            "source_kind": "results_page",
            "source_url": "https://www.syrahresources.com.au/investors/reports-presentations",
            "source_title": "Syrah Resources - Reports & Presentations",
        },
        "strategy": {
            "instrument": "SYR.ASX",
            "event_name": "SYR.ASX earnings",
            "scheduled_date": "2026-09-07",
            "consensus": {},
            "important_kpis": [],
            "bull_case": ["Bull"],
            "base_case": ["Base"],
            "bear_case": ["Bear"],
            "triggers": {},
            "invalidation_conditions": ["NO TRADE if evidence is incomplete"],
            "source_name": "Syrah Resources",
            "source_url": "https://www.syrahresources.com.au/investors/reports-presentations",
            "source_as_of": "2026-09-06",
            "change_note": "Reviewed assistant proposal",
            "summary": "Post-release confirmation strategy.",
            "assumptions": [],
            "unresolved_questions": [],
        },
    }


def _proposal(*, mode="demo", status="approved_for_materialization", payload=None, reviewer="marko"):
    return AssistantEventProposalRecord(
        id="00000000-0000-0000-0000-000000000321",
        proposal_key="SYR.ASX:earnings:2026-09-07",
        payload=payload or _payload(),
        requested_execution_mode=mode,
        status=status,
        reviewed_by=reviewer,
    )


class AssistantEventProposalTests(unittest.TestCase):
    def test_valid_reviewed_demo_proposal_uses_strategy_draft_validation(self) -> None:
        payload = validate_proposal_for_materialization(_proposal())
        self.assertEqual(payload.instrument, "SYR.ASX")
        self.assertEqual(payload.market, "Australia")
        self.assertEqual(payload.title, "Syrah Resources results")
        self.assertEqual(payload.strategy.event_name, "SYR.ASX earnings")

    def test_live_is_visible_metadata_but_fails_closed_for_materialization(self) -> None:
        with self.assertRaises(AssistantProposalLiveLocked):
            validate_proposal_for_materialization(_proposal(mode="live"))

    def test_only_review_approved_status_can_materialize(self) -> None:
        with self.assertRaises(AssistantProposalNotReady):
            validate_proposal_for_materialization(_proposal(status="ready_for_review"))
        with self.assertRaises(AssistantProposalNotReady):
            validate_proposal_for_materialization(_proposal(reviewer=None))

    def test_strategy_instrument_mismatch_fails_closed(self) -> None:
        payload = _payload()
        payload["strategy"] = dict(payload["strategy"])
        payload["strategy"]["instrument"] = "WRONG.ASX"
        with self.assertRaises(AssistantProposalIdentityConflict):
            validate_proposal_for_materialization(_proposal(payload=payload))

    def test_strategy_date_mismatch_fails_closed(self) -> None:
        payload = _payload()
        payload["strategy"] = dict(payload["strategy"])
        payload["strategy"]["scheduled_date"] = "2026-09-08"
        with self.assertRaises(AssistantProposalIdentityConflict):
            validate_proposal_for_materialization(_proposal(payload=payload))

    def test_event_at_must_be_timezone_aware(self) -> None:
        payload = _payload()
        payload["event_at"] = "2026-09-07T10:00:00"
        with self.assertRaises(ValidationError):
            validate_proposal_for_materialization(_proposal(payload=payload))

    def test_kind_must_be_explicit(self) -> None:
        payload = _payload()
        payload.pop("kind")
        with self.assertRaises(ValidationError):
            validate_proposal_for_materialization(_proposal(payload=payload))

    def test_official_source_must_pass_canonical_https_validation(self) -> None:
        for source_url in (
            " ",
            "http://www.syrahresources.com.au/investors/reports-presentations",
            "https://user:pass@www.syrahresources.com.au/investors/reports-presentations",
        ):
            with self.subTest(source_url=source_url):
                payload = _payload()
                payload["official_source"] = dict(payload["official_source"])
                payload["official_source"]["source_url"] = source_url
                with self.assertRaises(ValidationError):
                    validate_proposal_for_materialization(_proposal(payload=payload))

    def test_retry_audit_key_is_deterministic_and_proposal_scoped(self) -> None:
        self.assertEqual(
            proposal_approval_via(_proposal()),
            "assistant_proposal:00000000-0000-0000-0000-000000000321",
        )


if __name__ == "__main__":
    unittest.main()
