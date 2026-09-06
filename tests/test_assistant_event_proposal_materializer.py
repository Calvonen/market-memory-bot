from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import SimpleNamespace
import unittest

from trading_system.assistant_event_proposal_materializer import (
    AssistantEventProposalMaterializer,
    AssistantEventProposalRecord,
    AssistantProposalIdentityConflict,
    AssistantProposalLiveLocked,
    ExistingProposalApproval,
)
from trading_system.canonical_tracked_event_ingress import CanonicalTrackedEventWriteResult
from trading_system.etoro_instrument_resolver import ResolvedEtoroInstrument
from trading_system.models import EventExpectation
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceState,
)
from trading_system.strategy_draft import StrategyDraftPayload, draft_fingerprint, normalize_draft
from trading_system.strategy_draft_repository import (
    ExpectationVersionConflict,
    StrategyDraftApprovalResult,
)


def _payload() -> dict:
    return {
        "company_name": "Syrah Resources Limited",
        "instrument": "SYR.ASX",
        "market": "Australia",
        "kind": "earnings",
        "title": "Syrah Resources results",
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
            "consensus": {"revenue": 100.0},
            "important_kpis": ["revenue"],
            "bull_case": ["Revenue above expectations"],
            "base_case": ["Revenue near expectations"],
            "bear_case": ["Revenue below expectations"],
            "triggers": {"bull_revenue": 110.0, "bear_revenue": 90.0},
            "invalidation_conditions": ["Release evidence is incomplete"],
            "source_name": "Syrah Resources",
            "source_url": "https://www.syrahresources.com.au/investors/reports-presentations",
            "source_as_of": "2026-09-06",
            "change_note": "Assistant proposal for reviewed earnings setup",
            "summary": "Prepare a post-release confirmation strategy.",
            "assumptions": ["Release date remains estimated"],
            "unresolved_questions": [],
        },
    }


def _proposal(*, mode: str = "demo", status: str = "approved_for_materialization", payload=None):
    return AssistantEventProposalRecord(
        id="00000000-0000-0000-0000-000000000321",
        proposal_key="SYR.ASX:earnings:2026-09-07",
        payload=payload or _payload(),
        requested_execution_mode=mode,
        status=status,
        reviewed_by="marko",
    )


def _approval_fingerprint() -> str:
    strategy = StrategyDraftPayload.model_validate(_payload()["strategy"])
    return draft_fingerprint(normalize_draft("tracked:te-1", strategy))


class FakeProposals:
    def __init__(self, proposal: AssistantEventProposalRecord, approvals=None) -> None:
        self.proposal = proposal
        self.approvals = list(approvals or [])
        self.marked = False
        self.find_calls = 0

    def get(self, proposal_id: str):
        return self.proposal

    def find_approval(self, *, approved_via: str):
        self.find_calls += 1
        if not self.approvals:
            return None
        index = min(self.find_calls - 1, len(self.approvals) - 1)
        return self.approvals[index]

    def mark_materialized(self, proposal_id: str) -> None:
        self.marked = True


class FakeResolver:
    def __init__(self, result=None) -> None:
        self.calls = 0
        self.result = result or ResolvedEtoroInstrument(
            instrument_id=123,
            symbol="SYR.ASX",
            display_name="Syrah Resources Limited",
            market="Australia",
        )

    def resolve(self, request):
        self.calls += 1
        return self.result


class FakeInstruments:
    def __init__(self) -> None:
        self.calls = []

    def upsert(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="ti-1",
            instrument="SYR.ASX",
            market="Australia",
            active=True,
        )


class FakeEvents:
    def __init__(self) -> None:
        self.calls = []

    def register_for_tracked_instrument(self, tracked, **kwargs):
        self.calls.append((tracked, kwargs))
        return CanonicalTrackedEventWriteResult(
            event_id="te-1",
            tracked_instrument_id="ti-1",
            event_date=date(2026, 9, 7),
            action="inserted",
        )


class FakeReleaseShells:
    def __init__(self) -> None:
        self.targets = []

    def ensure_release_shell(self, target):
        self.targets.append(target)
        return target.event_id


class FakeExpectations:
    def get(self, event_id: str):
        return EventExpectation(
            event_id=event_id,
            instrument="SYR.ASX",
            event_name="SYR.ASX earnings",
            scheduled_date=date(2026, 9, 7),
            version=1,
        )


class FakeOfficialSources:
    def __init__(self, source=None) -> None:
        self.source = source
        self.set_calls = []
        self.get_calls = 0

    def get_state(self, event_id: str):
        self.get_calls += 1
        return OfficialReleaseSourceState(source=self.source, version=0 if self.source is None else 1)

    def set(self, source, *, expected_version: int, actor: str):
        self.set_calls.append((source, expected_version, actor))
        self.source = source
        return source


class FakeApprovals:
    def __init__(self, *, conflict: bool = False) -> None:
        self.calls = []
        self.conflict = conflict

    def approve(self, **kwargs):
        self.calls.append(kwargs)
        if self.conflict:
            raise ExpectationVersionConflict("simulated concurrent approval")
        return StrategyDraftApprovalResult(
            version=2,
            updated_at=datetime(2026, 9, 6, 6, 0, tzinfo=UTC),
        )


def _materializer(
    proposal: AssistantEventProposalRecord,
    *,
    proposal_approvals=None,
    official_source=None,
    approval_conflict: bool = False,
):
    proposals = FakeProposals(proposal, proposal_approvals)
    resolver = FakeResolver()
    instruments = FakeInstruments()
    events = FakeEvents()
    release_shells = FakeReleaseShells()
    expectations = FakeExpectations()
    official_sources = FakeOfficialSources(official_source)
    approvals = FakeApprovals(conflict=approval_conflict)
    materializer = AssistantEventProposalMaterializer(
        proposals=proposals,
        resolver=resolver,
        instruments=instruments,
        events=events,
        release_shells=release_shells,
        expectations=expectations,
        official_sources=official_sources,
        approvals=approvals,
    )
    return (
        materializer,
        proposals,
        resolver,
        instruments,
        events,
        release_shells,
        official_sources,
        approvals,
    )


class AssistantEventProposalMaterializerTests(unittest.TestCase):
    def test_live_proposal_fails_before_instrument_resolution(self) -> None:
        materializer, _, resolver, *rest = _materializer(_proposal(mode="live"))

        with self.assertRaises(AssistantProposalLiveLocked):
            materializer.materialize("proposal")

        self.assertEqual(resolver.calls, 0)

    def test_outer_strategy_identity_mismatch_fails_before_side_effects(self) -> None:
        payload = _payload()
        payload["strategy"] = dict(payload["strategy"])
        payload["strategy"]["instrument"] = "WRONG.ASX"
        materializer, _, resolver, *rest = _materializer(_proposal(payload=payload))

        with self.assertRaises(AssistantProposalIdentityConflict):
            materializer.materialize("proposal")

        self.assertEqual(resolver.calls, 0)

    def test_happy_path_reuses_canonical_boundaries_and_marks_materialized(self) -> None:
        (
            materializer,
            proposals,
            resolver,
            instruments,
            events,
            release_shells,
            official_sources,
            approvals,
        ) = _materializer(_proposal())

        result = materializer.materialize("proposal")

        self.assertEqual(result.action, "materialized")
        self.assertEqual(result.event_id, "tracked:te-1")
        self.assertEqual(result.expectation_version, 2)
        self.assertTrue(proposals.marked)
        self.assertEqual(resolver.calls, 1)
        self.assertEqual(instruments.calls[0]["source"], "manual")
        self.assertEqual(events.calls[0][1]["source"], "assistant_proposal")
        self.assertEqual(events.calls[0][1]["external_key"], _proposal().proposal_key)
        self.assertEqual(release_shells.targets[0].event_id, "tracked:te-1")
        self.assertEqual(len(official_sources.set_calls), 1)
        self.assertEqual(official_sources.set_calls[0][2], "marko")
        self.assertEqual(approvals.calls[0]["approved_by"], "marko")
        self.assertEqual(
            approvals.calls[0]["approved_via"],
            "assistant_proposal:00000000-0000-0000-0000-000000000321",
        )

    def test_retry_receipt_still_ensures_official_source_before_finalizing(self) -> None:
        existing = ExistingProposalApproval(
            event_id="tracked:te-1",
            expectation_version=2,
            draft_fingerprint=_approval_fingerprint(),
        )
        # First lookup is the top-of-method materialized check; second lookup
        # happens only after the official source has been ensured.
        materializer, proposals, _, _, _, _, official_sources, approvals = _materializer(
            _proposal(), proposal_approvals=[None, existing]
        )

        result = materializer.materialize("proposal")

        self.assertEqual(result.action, "recovered_retry")
        self.assertEqual(official_sources.get_calls, 1)
        self.assertEqual(len(official_sources.set_calls), 1)
        self.assertEqual(approvals.calls, [])
        self.assertTrue(proposals.marked)

    def test_concurrent_version_conflict_recovers_only_matching_proposal_audit(self) -> None:
        existing = ExistingProposalApproval(
            event_id="tracked:te-1",
            expectation_version=2,
            draft_fingerprint=_approval_fingerprint(),
        )
        materializer, proposals, _, _, _, _, _, approvals = _materializer(
            _proposal(),
            proposal_approvals=[None, None, existing],
            approval_conflict=True,
        )

        result = materializer.materialize("proposal")

        self.assertEqual(result.action, "recovered_concurrent_retry")
        self.assertEqual(result.expectation_version, 2)
        self.assertEqual(len(approvals.calls), 1)
        self.assertTrue(proposals.marked)

    def test_different_existing_official_source_fails_closed(self) -> None:
        existing_source = OfficialReleaseSource(
            event_id="tracked:te-1",
            source_kind="direct_url",
            source_url="https://example.com/different.pdf",
            source_title="Different source",
            version=1,
        )
        materializer, proposals, _, _, _, _, _, approvals = _materializer(
            _proposal(), official_source=existing_source
        )

        with self.assertRaises(AssistantProposalIdentityConflict):
            materializer.materialize("proposal")

        self.assertEqual(approvals.calls, [])
        self.assertFalse(proposals.marked)


if __name__ == "__main__":
    unittest.main()
