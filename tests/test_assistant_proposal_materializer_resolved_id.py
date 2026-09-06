from __future__ import annotations

import unittest
from types import SimpleNamespace

from trading_system.assistant_event_proposal import AssistantEventProposalRecord
from trading_system.assistant_proposal_materializer import (
    AssistantProposalInstrumentResolutionError,
    AssistantProposalMaterializer,
)
from trading_system.etoro_instrument_resolver import ResolvedEtoroInstrument


PROPOSAL_ID = "00000000-0000-0000-0000-000000000323"


def _proposal() -> AssistantEventProposalRecord:
    return AssistantEventProposalRecord(
        id=PROPOSAL_ID,
        proposal_key="SYR.ASX:earnings:2026-09-07",
        payload={
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
        },
        requested_execution_mode="demo",
        status="approved_for_materialization",
        reviewed_by="marko",
    )


class FakeProposals:
    def get(self, proposal_id: str):
        return _proposal() if proposal_id == PROPOSAL_ID else None


class FakeResolver:
    def __init__(self, instrument_id: int) -> None:
        self.instrument_id = instrument_id

    def resolve(self, request):
        return ResolvedEtoroInstrument(
            instrument_id=self.instrument_id,
            symbol="SYR.ASX",
            display_name="Syrah Resources Limited",
            market="Australia",
        )


class FakeRegistry:
    def __init__(self) -> None:
        self.calls = 0

    def upsert(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(id="unexpected", instrument="SYR.ASX", market="Australia")


class AssistantProposalResolvedIdTests(unittest.TestCase):
    def test_non_positive_resolved_ids_fail_before_first_canonical_write(self) -> None:
        for instrument_id in (0, -1):
            with self.subTest(instrument_id=instrument_id):
                registry = FakeRegistry()
                materializer = AssistantProposalMaterializer(
                    proposals=FakeProposals(),
                    resolver=FakeResolver(instrument_id),
                    registry=registry,
                    events=object(),
                    release_targets=object(),
                    expectations=object(),
                    official_sources=object(),
                    approvals=object(),
                )
                with self.assertRaises(AssistantProposalInstrumentResolutionError):
                    materializer.materialize(PROPOSAL_ID)
                self.assertEqual(registry.calls, 0)


if __name__ == "__main__":
    unittest.main()
