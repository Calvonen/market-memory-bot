from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tests.fixtures.hays_fy2026 import HAYS_FY2026
from trading_system.official_release_source_repository import (
    OfficialReleaseSource,
    OfficialReleaseSourceState,
)
from trading_system.tracked_event_release_ingestion import (
    ReleaseIngestionNotReady,
    ingest_tracked_event_release_once,
)
from trading_system.tracked_event_repository import (
    PersistentTrackedEvent,
    TrackedEventStatus,
    TrackedEventTimeStatus,
)


EVENT_ID = "11111111-1111-1111-1111-111111111111"
RELEASE_ID = f"tracked:{EVENT_ID}"


def _event() -> PersistentTrackedEvent:
    return PersistentTrackedEvent(
        event_id=EVENT_ID,
        tracked_instrument_id="22222222-2222-2222-2222-222222222222",
        calendar_event_id=None,
        company_name="Hays plc",
        instrument="HAS.L",
        market="LSE",
        source="manual",
        external_key="hays-fy26",
        kind="earnings",
        title="FY26 results",
        event_at=datetime(2026, 8, 20, tzinfo=UTC),
        event_time_status=TrackedEventTimeStatus.CONFIRMED,
        status=TrackedEventStatus.TRACKED,
    )


class _ExpectationRepository:
    def get(self, event_id):
        return replace(HAYS_FY2026, event_id=RELEASE_ID)


class _SourceRepository:
    def __init__(self, source):
        self.source = source

    def get_state(self, event_id):
        return OfficialReleaseSourceState(source=self.source, version=1)


class _AnalyzedReleaseRepository:
    def has_analysis_for_event_version(self, **kwargs):
        return True


def test_already_analyzed_returns_before_provider_construction(monkeypatch) -> None:
    source = OfficialReleaseSource(
        event_id=RELEASE_ID,
        source_kind="direct_url",
        source_url="https://example.com/results.pdf",
        version=1,
    )
    monkeypatch.setattr(
        "trading_system.tracked_event_release_ingestion.ManualOfficialReleaseProvider",
        lambda source: pytest.fail("provider must not be built"),
    )

    result = ingest_tracked_event_release_once(
        _event(),
        expectation_repository=_ExpectationRepository(),
        official_release_source_repository=_SourceRepository(source),
        release_repository=_AnalyzedReleaseRepository(),
        analyzer=object(),
    )

    assert result.status == "already_analyzed"
    assert result.release_event_id == RELEASE_ID


def test_missing_approved_source_fails_closed_even_when_already_analyzed() -> None:
    with pytest.raises(ReleaseIngestionNotReady, match="No active approved"):
        ingest_tracked_event_release_once(
            _event(),
            expectation_repository=_ExpectationRepository(),
            official_release_source_repository=_SourceRepository(None),
            release_repository=_AnalyzedReleaseRepository(),
            analyzer=object(),
        )
