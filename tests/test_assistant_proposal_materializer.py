from __future__ import annotations

import unittest
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
    AssistantProposalReviewedVersionConflict,
)
from trading_system.etoro_instrument_resolver import ResolvedEtoroInstrument
from trading_system.models import EventExpectation
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceState,
)
from trading_system.strategy_draft import StrategyDraftPayload, draft_fingerprint, normalize_draft
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
        "base_expectation_version": 1,
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


def _proposal(*, status: str = "approved_for_materialization") -> AssistantEventProposalRecord:
    return AssistantEventProposalRecord(
        id=PROPOSAL_ID,
        proposal_key="SYR.ASX:earnings:2026-09-07",
        payload=_payload(),
        requested_execution_mode="demo",
        status=status,
        reviewed_by="marko",
    )


def _expectation(*, version: int = 1, event_name: str = "SYR.ASX earnings") -> EventExpectation:
    return EventExpectation(
        event_id=EVENT_ID,
        instrument="SYR.ASX",
        event_name=event_name,
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
        version=version,
        updated_at=datetime(2026, 9, 6, tzinfo=UTC),
    )


class FakeProposals:
    def __init__(self, proposal: AssistantEventProposalRecord) -> None:
        self.proposal = proposal
        self.receipt: ExistingProposalApproval | None = None
        self.marked: list[str] = []

    def get(self, proposal_id: str):
        return self.proposal if proposal_id == self.proposal.id else None

    def find_approval(self, *, approved_via: str):
        return self.receipt

    def mark_materialized(self, proposal_id: str) -> None:
        self.marked.append(proposal_id)


class FakeResolver:
    def __init__(self) -> None:
        self.result: ResolvedEtoroInstrument | None = ResolvedEtoroInstrument(
            instrument_id=323,
            symbol="SYR.ASX",
            display_name="Syrah Resources Limited",
            market="Australia",
        )

    def resolve(self, request):
        return self.result


class FakeRegistry:
    def __init__(self) -> None:
        self.calls = 0
        self.row = SimpleNamespace(
            id="tracked-instrument-323",
            instrument="SYR.ASX",
            market="Australia",
        )

    def upsert(self, **kwargs):
        self.calls += 1
        return self.row


class FakeEvents:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.writes: list[dict] = []
        self.raise_version_conflict = False

    def register_assistant_proposal_for_tracked_instrument(self, tracked, **kwargs):
        self.calls.append(kwargs)
        if self.raise_version_conflict:
            raise RuntimeError("expectation_version_conflict: expected 1 but current is 2")
        self.writes.append(kwargs)
        return SimpleNamespace(event_id="tracked-323")


class FakeReleaseTargets:
    def __init__(self) -> None:
        self.event_id = EVENT_ID

    def ensure_release_shell(self, target):
        return self.event_id


class FakeExpectations:
    def __init__(self) -> None:
        self.current = _expectation()

    def get(self, event_id: str):
        return self.current


class FakeOfficialSources:
    def __init__(self) -> None:
        self.state = OfficialReleaseSourceState(source=None, version=0)
        self.actors: list[str] = []

    def get_state(self, event_id: str):
        return self.state

    def set(self, source, *, expected_version: int, actor: str):
        self.actors.append(actor)
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

    def approve_assistant_proposal(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_conflict:
            raise ExpectationVersionConflict("conflict")
        return SimpleNamespace(version=2)


class Harness:
    def __init__(self) -> None:
        self.proposals = FakeProposals(_proposal())
        self.resolver = FakeResolver()
        self.registry = FakeRegistry()
        self.events = FakeEvents()
        self.release_targets = FakeReleaseTargets()
        self.expectations = FakeExpectations()
        self.sources = FakeOfficialSources()
        self.approvals = FakeApprovals()
        self.materializer = AssistantProposalMaterializer(
            proposals=self.proposals,
            resolver=self.resolver,
            registry=self.registry,
            events=self.events,
            release_targets=self.release_targets,
            expectations=self.expectations,
            official_sources=self.sources,
            approvals=self.approvals,
        )


class AssistantProposalMaterializerTests(unittest.TestCase):
    def test_happy_path_uses_atomic_assistant_boundaries(self) -> None:
        h = Harness()
        result = h.materializer.materialize(PROPOSAL_ID)

        self.assertEqual(result.event_id, EVENT_ID)
        self.assertEqual(result.expectation_version, 2)
        self.assertEqual(h.events.calls[0]["proposal_id"], PROPOSAL_ID)
        self.assertEqual(h.events.calls[0]["expected_base_version"], 1)
        self.assertEqual(h.approvals.calls[0]["proposal_id"], PROPOSAL_ID)
        self.assertEqual(h.approvals.calls[0]["expected_base_version"], 1)
        self.assertEqual(
            h.sources.actors,
            [f"assistant_proposal:{PROPOSAL_ID}:reviewer:marko"],
        )
        self.assertEqual(h.proposals.marked, [PROPOSAL_ID])

    def test_stale_review_is_rejected_inside_event_cas_before_event_write(self) -> None:
        h = Harness()
        h.events.raise_version_conflict = True

        with self.assertRaises(AssistantProposalReviewedVersionConflict):
            h.materializer.materialize(PROPOSAL_ID)

        self.assertEqual(h.events.writes, [])
        self.assertEqual(h.approvals.calls, [])
        self.assertEqual(h.proposals.marked, [])

    def test_resolution_failure_happens_before_canonical_write(self) -> None:
        h = Harness()
        h.resolver.result = None
        with self.assertRaises(AssistantProposalInstrumentResolutionError):
            h.materializer.materialize(PROPOSAL_ID)
        self.assertEqual(h.registry.calls, 0)
        self.assertEqual(h.events.writes, [])

    def test_non_positive_resolved_id_happens_before_registry_write(self) -> None:
        for instrument_id in (0, -1):
            with self.subTest(instrument_id=instrument_id):
                h = Harness()
                h.resolver.result = ResolvedEtoroInstrument(
                    instrument_id=instrument_id,
                    symbol="SYR.ASX",
                    display_name="Syrah Resources Limited",
                    market="Australia",
                )
                with self.assertRaises(AssistantProposalInstrumentResolutionError):
                    h.materializer.materialize(PROPOSAL_ID)
                self.assertEqual(h.registry.calls, 0)

    def test_release_shell_identity_mismatch_blocks_source_and_approval(self) -> None:
        h = Harness()
        h.expectations.current = _expectation(event_name="WRONG earnings")
        with self.assertRaises(AssistantProposalCanonicalIdentityConflict):
            h.materializer.materialize(PROPOSAL_ID)
        self.assertEqual(h.sources.actors, [])
        self.assertEqual(h.approvals.calls, [])

    def test_existing_different_official_source_fails_without_approval(self) -> None:
        h = Harness()
        h.sources.state = OfficialReleaseSourceState(
            source=OfficialReleaseSource(
                event_id=EVENT_ID,
                source_kind="results_page",
                source_url="https://example.com/results",
                source_title="Different",
                version=1,
            ),
            version=1,
        )
        with self.assertRaises(AssistantProposalOfficialSourceConflict):
            h.materializer.materialize(PROPOSAL_ID)
        self.assertEqual(h.approvals.calls, [])

    def test_matching_receipt_is_idempotent(self) -> None:
        h = Harness()
        strategy = StrategyDraftPayload.model_validate(_payload()["strategy"])
        fingerprint = draft_fingerprint(normalize_draft(EVENT_ID, strategy))
        h.proposals.receipt = ExistingProposalApproval(EVENT_ID, 2, fingerprint)
        h.expectations.current = _expectation(version=2)

        result = h.materializer.materialize(PROPOSAL_ID)
        self.assertTrue(result.retried)
        self.assertEqual(result.expectation_version, 2)
        self.assertEqual(h.approvals.calls, [])
        self.assertEqual(h.proposals.marked, [PROPOSAL_ID])

    def test_materialized_retry_uses_receipt_without_new_writes(self) -> None:
        h = Harness()
        h.proposals.proposal = _proposal(status="materialized")
        strategy = StrategyDraftPayload.model_validate(_payload()["strategy"])
        fingerprint = draft_fingerprint(normalize_draft(EVENT_ID, strategy))
        h.proposals.receipt = ExistingProposalApproval(EVENT_ID, 2, fingerprint)

        result = h.materializer.materialize(PROPOSAL_ID)
        self.assertTrue(result.retried)
        self.assertEqual(h.registry.calls, 0)
        self.assertEqual(h.events.writes, [])
        self.assertEqual(h.approvals.calls, [])
        self.assertEqual(h.proposals.marked, [])

    def test_strategy_cas_conflict_recovers_only_from_matching_receipt(self) -> None:
        h = Harness()
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
        try:
            result = h.materializer.materialize(PROPOSAL_ID)
        finally:
            h.proposals.find_approval = original_find  # type: ignore[method-assign]

        self.assertTrue(result.retried)
        self.assertEqual(result.expectation_version, 2)
        self.assertEqual(h.proposals.marked, [PROPOSAL_ID])


if __name__ == "__main__":
    unittest.main()
