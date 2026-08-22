from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from trading_system.tracked_instruments import (
    TrackedInstrument,
    TrackedInstrumentSource,
    add_tracking_source,
    create_tracked_instrument,
    set_tracking_active,
)


def test_create_tracked_instrument_normalises_symbol_and_preserves_metadata() -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)

    tracked = create_tracked_instrument(
        instrument=" nokia.he ",
        company_name=" Nokia Oyj ",
        market=" Helsinki ",
        source=TrackedInstrumentSource.SCANNER,
        now=now,
    )

    assert tracked.instrument == "NOKIA.HE"
    assert tracked.company_name == "Nokia Oyj"
    assert tracked.market == "Helsinki"
    assert tracked.sources == (TrackedInstrumentSource.SCANNER,)
    assert tracked.active is True
    assert tracked.created_at == now
    assert tracked.updated_at == now


def test_tracking_key_keeps_same_symbol_on_different_markets_distinct() -> None:
    london = create_tracked_instrument(
        instrument="ABC",
        market="London Stock Exchange",
        source=TrackedInstrumentSource.MANUAL,
    )
    nasdaq = create_tracked_instrument(
        instrument="abc",
        market="NASDAQ",
        source=TrackedInstrumentSource.MANUAL,
    )

    assert london.key != nasdaq.key


def test_tracking_key_normalises_case_and_whitespace() -> None:
    first = create_tracked_instrument(
        instrument=" abc.l ",
        market=" London   Stock Exchange ",
        source=TrackedInstrumentSource.CALENDAR,
    )
    second = create_tracked_instrument(
        instrument="ABC.L",
        market="london stock exchange",
        source=TrackedInstrumentSource.SCANNER,
    )

    assert first.key == second.key


def test_add_tracking_source_is_idempotent_and_does_not_create_event_state() -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    tracked = create_tracked_instrument(
        instrument="AAPL",
        company_name="Apple Inc",
        market="NASDAQ",
        source=TrackedInstrumentSource.SCANNER,
        now=now,
    )

    unchanged = add_tracking_source(tracked, TrackedInstrumentSource.SCANNER, now=now + timedelta(minutes=1))
    assert unchanged is tracked

    updated = add_tracking_source(tracked, TrackedInstrumentSource.CALENDAR, now=now + timedelta(minutes=2))
    assert updated.sources == (
        TrackedInstrumentSource.SCANNER,
        TrackedInstrumentSource.CALENDAR,
    )
    assert updated.updated_at == now + timedelta(minutes=2)
    assert not hasattr(updated, "event_id")
    assert not hasattr(updated, "expectation")
    assert not hasattr(updated, "strategy")


def test_add_tracking_source_reactivates_inactive_instrument() -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    tracked = create_tracked_instrument(
        instrument="MSFT",
        source=TrackedInstrumentSource.MANUAL,
        now=now,
    )
    inactive = set_tracking_active(tracked, False, now=now + timedelta(minutes=1))

    reactivated = add_tracking_source(
        inactive,
        TrackedInstrumentSource.SCANNER,
        now=now + timedelta(minutes=2),
    )

    assert reactivated.active is True
    assert reactivated.sources == (
        TrackedInstrumentSource.MANUAL,
        TrackedInstrumentSource.SCANNER,
    )
    assert reactivated.tracked_instrument_id == tracked.tracked_instrument_id
    assert reactivated.created_at == tracked.created_at


def test_set_tracking_active_preserves_identity_and_sources() -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    tracked = create_tracked_instrument(
        instrument="SAP.DE",
        company_name="SAP SE",
        market="Xetra",
        source=TrackedInstrumentSource.CALENDAR,
        now=now,
    )

    inactive = set_tracking_active(tracked, False, now=now + timedelta(minutes=1))

    assert inactive.active is False
    assert inactive.sources == tracked.sources
    assert inactive.tracked_instrument_id == tracked.tracked_instrument_id
    assert inactive.created_at == tracked.created_at
    assert inactive.updated_at == now + timedelta(minutes=1)


def test_blank_instrument_fails_closed() -> None:
    with pytest.raises(ValueError, match="instrument is required"):
        create_tracked_instrument(
            instrument="   ",
            source=TrackedInstrumentSource.MANUAL,
        )


def test_direct_construction_normalises_instrument_and_market() -> None:
    tracked = TrackedInstrument(instrument=" aapl ", market=" nasdaq ")

    assert tracked.instrument == "AAPL"
    assert tracked.market == "nasdaq"


def test_direct_construction_with_blank_instrument_fails_closed() -> None:
    with pytest.raises(ValueError, match="instrument is required"):
        TrackedInstrument(instrument="   ")


def test_replace_with_blank_instrument_fails_closed() -> None:
    tracked = create_tracked_instrument(
        instrument="AAPL",
        source=TrackedInstrumentSource.MANUAL,
    )

    with pytest.raises(ValueError, match="instrument is required"):
        replace(tracked, instrument="   ")


def test_replace_normalises_instrument_through_post_init() -> None:
    tracked = create_tracked_instrument(
        instrument="AAPL",
        source=TrackedInstrumentSource.MANUAL,
    )

    renamed = replace(tracked, instrument=" msft ")

    assert renamed.instrument == "MSFT"


def test_add_tracking_source_reactivates_without_duplicating_existing_source() -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    tracked = create_tracked_instrument(
        instrument="MSFT",
        source=TrackedInstrumentSource.MANUAL,
        now=now,
    )
    inactive = set_tracking_active(tracked, False, now=now + timedelta(minutes=1))

    reactivated = add_tracking_source(
        inactive,
        TrackedInstrumentSource.MANUAL,
        now=now + timedelta(minutes=2),
    )

    assert reactivated.active is True
    assert reactivated.sources == (TrackedInstrumentSource.MANUAL,)
    assert reactivated.tracked_instrument_id == tracked.tracked_instrument_id
    assert reactivated.created_at == tracked.created_at
