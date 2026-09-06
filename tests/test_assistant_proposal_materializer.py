from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import SimpleNamespace

from trading_system.assistant_event_proposal import (
    AssistantEventProposalRecord,
    ExistingProposalApproval,
)
from trading_system.assistant_proposal_materializer import (
    AssistantProposalCanonicalIdentityConflict,
    AssistantProposalInstrumentResolutionError,
    AssistantProposalMaterializer,
    AssistantProposalOfficialSourceConflict,
)
from trading_system.etoro_instrument_resolver import ResolvedEtoroInstrument
from trading_system.models import EventExpectation
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceState,
    OfficialReleaseSourceVersionConflict,
)
from trading_system.strategy_draft_repository import ExpectationVersionConflict


PROPOSAL_ID = "00000000-0000-0000-0000-000000000323"
EVENT_ID = "tracked:tracked-323"


def _payload() -> dict:
    return {
        "company_name": "Syrah Resources Limited",
        "instrument": "SYR.ASX",
        "market": "Australia",
        "kind": "earnings",
        "title": "Syrah Resources results",
        "scheduled_date": "2026-09-07",
        "event_at": "2026-09-07T00:00:00+00:00",
        "event_time_status": "unknown",
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


def _proposal() -> AssistantEventProposalRecord:
    return AssistantEventProposalRecord(
        id=PROPOSAL_ID,
        proposal_key="SYR.ASX:earnings:2026-09-07",
        payload=_payload(),
        requested_execution_mode="demo",
        status="approved_for_materialization",
        reviewed_by="marko",
    )


def _expectation() -> EventExpectation:
    return EventExpectation(
        event_id=EVENT_ID,
        instrument="SYR.ASX",
        event_name="SYR.ASX earnings",
        scheduled_date=date(2026, 9, 7),
        consensus={},
        important_kpis=(),
        bull_case=(),
        base_case=(),
        bear_case=(),
        triggers={},
        invalidation_conditions=(),
        source_name=None,
        source_url=None,
        source_as_of=None,
        version=1,
        updated_at=datetime(2026, 9, 6, tzinfo=UTC),
    )


class FakeProposals:
    def __init__(self, proposal: AssistantEventProposalRecord) -> None:
        self.proposal = proposal
        self.receipt: ExistingProposalApproval | None = None
        self.marked: list[str] = []
        self.calls: list[str] = []

    def get(self, proposal_id: str):
        self.calls.append("proposal.get")
        return self.proposal if proposal_id == self.proposal.id else None

    def find_approval(self, *, approved_via: str):
        self.calls.append("proposal.find_approval")
        return self.receipt

    def mark_materialized(self, proposal_id: str) -> None:
        self.calls.append("proposal.mark_materialized")
        self.marked.append(proposal_id)


class FakeResolver:
    def __init__(self) -> None:
        self.result: ResolvedEtoroInstrument | None = ResolvedEtoroInstrument(
            instrument_id=323,
            symbol="SYR.ASX",
            display_name="Syrah Resources Limited",
            market="Australia",
        )
        self.calls: list[str] = []

    def resolve(self, request):
        self.calls.append("resolver.resolve")
        return self.result


class FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.row = SimpleNamespace(
            id="tracked-instrument-323",
            instrument="SYR.ASX",
            market="Australia",
        )

    def upsert(self, **kwargs):
        self.calls.append("registry.upsert")
        return self.row


class FakeEvents:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def register_for_tracked_instrument(self, tracked, **kwargs):
        self.calls.append(("events.register", kwargs["event_time_status"]))
        return SimpleNamespace(event_id="tracked-323")


class FakeReleaseTargets:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.event_id = EVENT_ID

    def ensure_release_shell(self, target):
        self.calls.append("release.ensure_shell")
        return self.event_id


class FakeExpectations:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.current = _expectation()

    def get(self, event_id: str):
        self.calls.append("expectations.get")
        return self.current


class FakeOfficialSources:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.state = OfficialReleaseSourceState(source=None, version=0)
        self.raise_conflict = False
        self.latest_after_conflict: OfficialReleaseSourceState | None = None

    def get_state(self, event_id: str):
        self.calls.append("source.get_state")
        if self.raise_conflict and self.latest_after_conflict is not None and self.calls.count("source.get_state") > 1:
            return self.latest_after_conflict
        return self.state

    def set(self, source, *, expected_version: int, actor: str):
        self.calls.append("source.set")
        if self.raise_conflict:
            raise OfficialReleaseSourceVersionConflict("conflict")
        self.state = OfficialReleaseSourceState(
            source=OfficialReleaseSource(
                event_id=source.event_id,
                source_kind=source.source_kind,
                source_url=source.source_url,
                source_title=source.source_title,
                version=expected_version + 1,
            ),
            version=expected_version + 1,
        )
        return self.state.source


class FakeApprovals:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.raise_conflict = False

    def approve(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_conflict:
            raise ExpectationVersionConflict("conflict")
        return SimpleNamespace(version=2)


@dataclass
class Harness:
    proposals: FakeProposals
    resolver: FakeResolver
    registry: FakeRegistry
    events: FakeEvents
    release_targets: FakeReleaseTargets
    expectations: FakeExpectations
    sources: FakeOfficialSources
    approvals: FakeApprovals
    materializer: AssistantProposalMaterializer


def _harness() -> Harness:
    proposals = FakeProposals(_proposal())
    resolver = FakeResolver()
    registry = FakeRegistry()
    events = FakeEvents()
    release_targets = FakeReleaseTargets()
    expectations = FakeExpectations()
    sources = FakeOfficialSources()
    approvals = FakeApprovals()
    materializer = AssistantProposalMaterializer(
        proposals=proposals,
        resolver=resolver,
        registry=registry,
        events=events,
        release_targets=release_targets,
        expectations=expectations,
        official_sources=sources,
        approvals=approvals,
    )
    return Harness(
        proposals,
        resolver,
        registry,
        events,
        release_targets,
        expectations,
        sources,
        approvals,
        materializer,
    )


class AssistantProposalMaterializerTests(unittest.TestCase):
    def test_happy_path_uses_canonical_layers_and_preserves_unknown_time(self) -> None:
        h = _harness()
        result = h.materializer.materialize(PROPOSAL_ID)

        self.assertEqual(result.event_id, EVENT_ID)
        self.assertEqual(result.expectation_version, 2)
        self.assertFalse(result.retried)
        self.assertEqual(h.proposals.marked, [PROPOSAL_ID])
        self.assertEqual(str(h.events.calls[0][1].value), "unknown")
        self.assertEqual(len(h.approvals.calls), 1)
        self.assertEqual(h.approvals.calls[0]["approved_via"], f"assistant_proposal:{PROPOSAL_ID}")
        self.assertEqual(h.approvals.calls[0]["approved_by"], "marko")

    def test_resolution_failure_happens_before_any_canonical_write(self) -> None:
        h = _harness()
        h.resolver.result = None

        with self.assertRaises(AssistantProposalInstrumentResolutionError):
            h.materializer.materialize(PROPOSAL_ID)

        self.assertEqual(h.registry.calls, [])
        self.assertEqual(h.events.calls, [])
        self.assertEqual(h.sources.calls, [])
        self.assertEqual(h.approvals.calls, [])
        self.assertEqual(h.proposals.marked, [])

    def test_resolved_symbol_mismatch_fails_before_registry_write(self) -> None:
        h = _harness()
        h.resolver.result = ResolvedEtoroInstrument(
            instrument_id=999,
            symbol="WRONG.ASX",
            display_name="Syrah Resources Limited",
            market="Australia",
        )

        with self.assertRaises(AssistantProposalCanonicalIdentityConflict):
            h.materializer.materialize(PROPOSAL_ID)

        self.assertEqual(h.registry.calls, [])
        self.assertEqual(h.events.calls, [])

    def test_release_shell_identity_mismatch_blocks_source_and_approval(self) -> None:
        h = _harness()
        h.expectations.current = EventExpectation(
            **{**_expectation().__dict__, "event_name": "WRONG earnings"}
        )

        with self.assertRaises(AssistantProposalCanonicalIdentityConflict):
            h.materializer.materialize(PROPOSAL_ID)

        self.assertEqual(h.sources.calls, [])
        self.assertEqual(h.approvals.calls, [])
        self.assertEqual(h.proposals.marked, [])

    def test_existing_matching_receipt_is_idempotent_but_source_is_still_ensured(self) -> None:
        h = _harness()
        first = h.materializer.materialize(PROPOSAL_ID)
        fingerprint = h.approvals.calls[0]["draft_fingerprint"]

        h.proposals.marked.clear()
        h.approvals.calls.clear()
        h.proposals.receipt = ExistingProposalApproval(
            event_id=EVENT_ID,
            expectation_version=2,
            draft_fingerprint=fingerprint,
        )
        second = h.materializer.materialize(PROPOSAL_ID)

        self.assertTrue(second.retried)
        self.assertEqual(second.expectation_version, 2)
        self.assertEqual(h.approvals.calls, [])
        self.assertIn("source.get_state", h.sources.calls)
        self.assertEqual(h.proposals.marked, [PROPOSAL_ID])

    def test_receipt_with_wrong_event_or_fingerprint_fails_closed(self) -> None:
        h = _harness()
        h.proposals.receipt = ExistingProposalApproval(
            event_id="tracked:wrong",
            expectation_version=2,
            draft_fingerprint="wrong",
        )

        with self.assertRaises(AssistantProposalCanonicalIdentityConflict):
            h.materializer.materialize(PROPOSAL_ID)

        self.assertEqual(h.approvals.calls, [])
        self.assertEqual(h.proposals.marked, [])

    def test_strategy_cas_conflict_recovers_only_from_matching_receipt(self) -> None:
        h = _harness()
        h.approvals.raise_conflict = True

        original_find = h.proposals.find_approval
        calls = 0

        def find_after_conflict(*, approved_via: str):
            nonlocal calls
            calls += 1
            if calls == 1:
                return None
            fingerprint = h.approvals.calls[0]["draft_fingerprint"]
            return ExistingProposalApproval(EVENT_ID, 2, fingerprint)

        h.proposals.find_approval = find_after_conflict  # type: ignore[method-assign]
        result = h.materializer.materialize(PROPOSAL_ID)
        h.proposals.find_approval = original_find  # type: ignore[method-assign]

        self.assertEqual(result.expectation_version, 2)
        self.assertEqual(h.proposals.marked, [PROPOSAL_ID])

    def test_existing_different_official_source_fails_without_approval(self) -> None:
        h = _harness()
        h.sources.state = OfficialReleaseSourceState(
            source=OfficialReleaseSource(
                event_id=EVENT_ID,
                source_kind="results_page",
                source_url="https://example.com/results",
                source_title="Different source",
                version=1,
            ),
            version=1,
        )

        with self.assertRaises(AssistantProposalOfficialSourceConflict):
            h.materializer.materialize(PROPOSAL_ID)

        self.assertEqual(h.approvals.calls, [])
        self.assertEqual(h.proposals.marked, [])

    def test_source_cas_conflict_accepts_only_same_concurrent_source(self) -> None:
        h = _harness()
        h.sources.raise_conflict = True
        desired = OfficialReleaseSource(
            event_id=EVENT_ID,
            source_kind="results_page",
            source_url="https://www.syrahresources.com.au/investors/reports-presentations",
            source_title="Syrah Resources - Reports & Presentations",
            version=1,
        )
        h.sources.latest_after_conflict = OfficialReleaseSourceState(source=desired, version=1)

        result = h.materializer.materialize(PROPOSAL_ID)
        self.assertEqual(result.expectation_version, 2)
        self.assertEqual(len(h.approvals.calls), 1)


if __name__ == "__main__":
    unittest.main()
