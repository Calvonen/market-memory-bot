from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.tracked_etoro_orchestrator import stream_tracked_etoro_instruments
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


def _tracked(
    tracked_id: str,
    etoro_id: int,
    instrument: str,
    market: str = "",
) -> TrackedEtoroInstrument:
    return TrackedEtoroInstrument(
        tracked_instrument_id=tracked_id,
        instrument=instrument,
        market=market,
        etoro_instrument_id=etoro_id,
        etoro_symbol=instrument,
        etoro_display_name=instrument,
    )


def _update(instrument_id: int, price: str) -> EtoroMarketUpdate:
    value = Decimal(price)
    return EtoroMarketUpdate(
        instrument_id=instrument_id,
        bid=value,
        ask=value,
        last_execution=value,
        timestamp=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
        is_market_open=True,
        is_exchange_open=True,
        message_type="Update",
    )


class StubProvider:
    def __init__(self, streams: dict[int, list[EtoroMarketUpdate] | Exception]) -> None:
        self.streams = streams
        self.calls: list[tuple[int, bool]] = []

    async def stream_instrument(self, instrument_id: int, *, reconnect: bool = True):
        self.calls.append((instrument_id, reconnect))
        configured = self.streams[instrument_id]
        if isinstance(configured, Exception):
            raise configured
        for update in configured:
            await asyncio.sleep(0)
            yield update


@pytest.mark.asyncio
async def test_orchestrator_merges_multiple_tracked_streams_and_preserves_identity() -> None:
    first = _tracked("tracked-1", 101, "AAA", "LSE")
    second = _tracked("tracked-2", 202, "BBB", "NASDAQ")
    provider = StubProvider(
        {
            101: [_update(101, "10")],
            202: [_update(202, "20")],
        }
    )

    received = [
        item
        async for item in stream_tracked_etoro_instruments(
            (first, second),
            provider,
            reconnect=False,
        )
    ]

    assert {(item.tracked_instrument_id, item.etoro_instrument_id) for item in received} == {
        ("tracked-1", 101),
        ("tracked-2", 202),
    }
    assert {(item.instrument, item.market) for item in received} == {
        ("AAA", "LSE"),
        ("BBB", "NASDAQ"),
    }
    assert set(provider.calls) == {(101, False), (202, False)}


@pytest.mark.asyncio
async def test_orchestrator_accepts_empty_input_without_starting_streams() -> None:
    provider = StubProvider({})

    received = [item async for item in stream_tracked_etoro_instruments((), provider)]

    assert received == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_duplicate_tracked_identity_fails_closed_before_streaming() -> None:
    provider = StubProvider({101: [], 202: []})
    first = _tracked("same", 101, "AAA")
    second = _tracked("same", 202, "BBB")

    with pytest.raises(ValueError, match="duplicate tracked_instrument_id"):
        _ = [item async for item in stream_tracked_etoro_instruments((first, second), provider)]

    assert provider.calls == []


@pytest.mark.asyncio
async def test_duplicate_etoro_identity_fails_closed_before_streaming() -> None:
    provider = StubProvider({101: []})
    first = _tracked("tracked-1", 101, "AAA")
    second = _tracked("tracked-2", 101, "AAA.L")

    with pytest.raises(ValueError, match="duplicate etoro_instrument_id"):
        _ = [item async for item in stream_tracked_etoro_instruments((first, second), provider)]

    assert provider.calls == []


@pytest.mark.asyncio
async def test_worker_failure_is_propagated_and_sibling_streams_are_cancelled() -> None:
    failed = _tracked("tracked-1", 101, "AAA")
    sibling = _tracked("tracked-2", 202, "BBB")

    class FailingProvider(StubProvider):
        def __init__(self) -> None:
            super().__init__({101: RuntimeError("stream failed"), 202: []})
            self.sibling_cancelled = False

        async def stream_instrument(self, instrument_id: int, *, reconnect: bool = True):
            self.calls.append((instrument_id, reconnect))
            if instrument_id == 101:
                await asyncio.sleep(0)
                raise RuntimeError("stream failed")
            try:
                while True:
                    await asyncio.sleep(3600)
                    if False:
                        yield _update(202, "20")
            finally:
                self.sibling_cancelled = True

    provider = FailingProvider()

    with pytest.raises(RuntimeError, match="stream failed"):
        _ = [item async for item in stream_tracked_etoro_instruments((failed, sibling), provider)]

    assert set(provider.calls) == {(101, True), (202, True)}
    assert provider.sibling_cancelled is True
