from trading_system.etoro_instrument_resolver import (
    InstrumentResolutionRequest,
    ResolvedEtoroInstrument,
)
from trading_system.tracked_instrument_etoro import resolve_tracked_instrument
from trading_system.tracked_instruments import (
    TrackedInstrumentSource,
    create_tracked_instrument,
    set_tracking_active,
)


class _ResolverStub:
    def __init__(self, result: ResolvedEtoroInstrument | None) -> None:
        self.result = result
        self.calls: list[InstrumentResolutionRequest] = []

    def resolve(self, request: InstrumentResolutionRequest) -> ResolvedEtoroInstrument | None:
        self.calls.append(request)
        return self.result


def test_active_tracked_instrument_is_forwarded_to_resolver() -> None:
    tracked = create_tracked_instrument(
        instrument=" nokia.he ",
        company_name="Nokia Oyj",
        market="Helsinki",
        source=TrackedInstrumentSource.SCANNER,
    )
    resolver = _ResolverStub(ResolvedEtoroInstrument(2001, "NOKIA", "Nokia Oyj", "eToro Helsinki"))

    mapped = resolve_tracked_instrument(tracked, resolver)

    assert resolver.calls == [
        InstrumentResolutionRequest(
            instrument="NOKIA.HE",
            company_name="Nokia Oyj",
            market="Helsinki",
        )
    ]
    assert mapped is not None
    assert mapped.tracked_instrument_id == tracked.tracked_instrument_id
    assert mapped.instrument == "NOKIA.HE"
    assert mapped.market == "Helsinki"
    assert mapped.etoro_instrument_id == 2001
    assert mapped.etoro_symbol == "NOKIA"
    assert mapped.etoro_display_name == "Nokia Oyj"


def test_missing_tracked_market_is_filled_from_exact_etoro_resolution() -> None:
    tracked = create_tracked_instrument(
        instrument="BTC",
        source=TrackedInstrumentSource.MANUAL,
    )
    resolver = _ResolverStub(ResolvedEtoroInstrument(100000, "BTC", "Bitcoin", "Digital Currency"))

    mapped = resolve_tracked_instrument(tracked, resolver)

    assert mapped is not None
    assert mapped.market == "Digital Currency"
    assert mapped.etoro_instrument_id == 100000


def test_unresolved_instrument_propagates_none() -> None:
    tracked = create_tracked_instrument(
        instrument="ABC.L",
        company_name="Example PLC",
        market="UK",
        source=TrackedInstrumentSource.CALENDAR,
    )
    resolver = _ResolverStub(None)

    assert resolve_tracked_instrument(tracked, resolver) is None
    assert len(resolver.calls) == 1


def test_inactive_instrument_is_not_resolved() -> None:
    tracked = create_tracked_instrument(
        instrument="AAPL",
        company_name="Apple Inc",
        market="US",
        source=TrackedInstrumentSource.MANUAL,
    )
    tracked = set_tracking_active(tracked, False)
    resolver = _ResolverStub(ResolvedEtoroInstrument(1001, "AAPL", "Apple Inc"))

    assert resolve_tracked_instrument(tracked, resolver) is None
    assert resolver.calls == []


def test_tracking_source_does_not_change_resolution_contract() -> None:
    scanner = create_tracked_instrument(
        instrument="MSFT",
        company_name="Microsoft Corporation",
        market="US",
        source=TrackedInstrumentSource.SCANNER,
    )
    manual = create_tracked_instrument(
        instrument="MSFT",
        company_name="Microsoft Corporation",
        market="US",
        source=TrackedInstrumentSource.MANUAL,
    )
    result = ResolvedEtoroInstrument(1002, "MSFT", "Microsoft Corporation")
    scanner_resolver = _ResolverStub(result)
    manual_resolver = _ResolverStub(result)

    scanner_mapping = resolve_tracked_instrument(scanner, scanner_resolver)
    manual_mapping = resolve_tracked_instrument(manual, manual_resolver)

    assert scanner_resolver.calls[0] == manual_resolver.calls[0]
    assert scanner_mapping is not None
    assert manual_mapping is not None
    assert scanner_mapping.etoro_instrument_id == manual_mapping.etoro_instrument_id == 1002
