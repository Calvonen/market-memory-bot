from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_system.tracked_instrument_etoro import (
    InstrumentResolver,
    TrackedEtoroInstrument,
    resolve_tracked_instrument,
)
from trading_system.tracked_instrument_registry import TrackedInstrumentRecord
from trading_system.tracked_instruments import TrackedInstrument, TrackedInstrumentSource
from trading_system.tracking_profile_registry import TrackedInstrumentProfileRecord


PROFILE_BATCH_SIZE = 50
_TREND_TARGET_SELECTION_PROOF = object()


class ActiveTrackedInstrumentReader(Protocol):
    def list_active(self) -> list[TrackedInstrumentRecord]: ...


class TrackingProfileBatchReader(Protocol):
    def list_for_instruments(
        self, tracked_instrument_ids: list[str]
    ) -> dict[str, list[TrackedInstrumentProfileRecord]]: ...


@dataclass(frozen=True, init=False)
class TrendMonitoringTargets:
    """Provenance-bearing snapshot produced only by canonical target selection."""

    resolved: tuple[TrackedEtoroInstrument, ...]
    unresolved_tracked_instrument_ids: tuple[str, ...]

    def __init__(
        self,
        resolved: tuple[TrackedEtoroInstrument, ...],
        unresolved_tracked_instrument_ids: tuple[str, ...],
        *,
        _selection_proof: object,
    ) -> None:
        if _selection_proof is not _TREND_TARGET_SELECTION_PROOF:
            raise ValueError("Trend monitoring targets require canonical selection proof")
        object.__setattr__(self, "resolved", resolved)
        object.__setattr__(
            self,
            "unresolved_tracked_instrument_ids",
            unresolved_tracked_instrument_ids,
        )


def _selected_targets(
    *,
    resolved: tuple[TrackedEtoroInstrument, ...],
    unresolved_tracked_instrument_ids: tuple[str, ...],
) -> TrendMonitoringTargets:
    return TrendMonitoringTargets(
        resolved=resolved,
        unresolved_tracked_instrument_ids=unresolved_tracked_instrument_ids,
        _selection_proof=_TREND_TARGET_SELECTION_PROOF,
    )


def _domain_tracked(record: TrackedInstrumentRecord) -> TrackedInstrument:
    sources: list[TrackedInstrumentSource] = []
    for raw_source in record.sources:
        try:
            sources.append(TrackedInstrumentSource(raw_source))
        except ValueError:
            continue
    return TrackedInstrument(
        instrument=record.instrument,
        company_name=record.company_name,
        market=record.market,
        sources=tuple(sources),
        active=record.active,
        tracked_instrument_id=record.id,
    )


def _trend_enabled(profiles: list[TrackedInstrumentProfileRecord]) -> bool:
    trend_profiles = [profile for profile in profiles if profile.profile_type == "trend"]
    if len(trend_profiles) > 1:
        raise RuntimeError("multiple trend profiles for tracked instrument")
    return bool(trend_profiles and trend_profiles[0].enabled)


def _read_profiles_in_batches(
    profile_reader: TrackingProfileBatchReader,
    ids: list[str],
) -> dict[str, list[TrackedInstrumentProfileRecord]]:
    profiles_by_id: dict[str, list[TrackedInstrumentProfileRecord]] = {}
    for offset in range(0, len(ids), PROFILE_BATCH_SIZE):
        batch_ids = ids[offset : offset + PROFILE_BATCH_SIZE]
        batch_profiles = profile_reader.list_for_instruments(batch_ids)
        if set(batch_profiles) != set(batch_ids):
            raise RuntimeError("tracking profile batch did not match active instruments")
        overlap = set(profiles_by_id).intersection(batch_profiles)
        if overlap:
            raise RuntimeError("tracking profile batches returned duplicate instrument ids")
        profiles_by_id.update(batch_profiles)
    if set(profiles_by_id) != set(ids):
        raise RuntimeError("tracking profile batch did not match active instruments")
    return profiles_by_id


def select_trend_monitoring_targets(
    tracked_reader: ActiveTrackedInstrumentReader,
    profile_reader: TrackingProfileBatchReader,
    resolver: InstrumentResolver,
) -> TrendMonitoringTargets:
    """Select the canonical active + enabled Trend instruments for monitoring.

    This is a read/selection boundary only. It does not create profiles or events,
    open market-data streams, persist observations, or invoke trading behavior.
    Instruments whose eToro identity cannot be resolved remain explicit in the
    unresolved list instead of being guessed or silently treated as monitorable.
    """
    active_records = tracked_reader.list_active()
    if not active_records:
        return _selected_targets(resolved=(), unresolved_tracked_instrument_ids=())

    ids = [record.id.strip() for record in active_records]
    if any(not tracked_id for tracked_id in ids):
        raise RuntimeError("active tracked instrument has blank id")
    if len(set(ids)) != len(ids):
        raise RuntimeError("duplicate active tracked instrument id")
    if any(not record.active for record in active_records):
        raise RuntimeError("active tracked reader returned inactive instrument")

    profiles_by_id = _read_profiles_in_batches(profile_reader, ids)

    resolved: list[TrackedEtoroInstrument] = []
    unresolved: list[str] = []
    seen_etoro_ids: set[int] = set()

    for record in active_records:
        profiles = profiles_by_id[record.id]
        if not _trend_enabled(profiles):
            continue

        tracked = _domain_tracked(record)
        target = resolve_tracked_instrument(tracked, resolver)
        if target is None:
            unresolved.append(record.id)
            continue
        if target.etoro_instrument_id in seen_etoro_ids:
            raise RuntimeError("duplicate resolved eToro instrument id")
        seen_etoro_ids.add(target.etoro_instrument_id)
        resolved.append(target)

    return _selected_targets(
        resolved=tuple(resolved),
        unresolved_tracked_instrument_ids=tuple(unresolved),
    )
