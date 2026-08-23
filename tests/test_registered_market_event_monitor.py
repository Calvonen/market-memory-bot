from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.etoro_market_data import EtoroMarketUpdate
from trading_system.market_event import MarketEvent, MarketEventKind, MarketEventSource
from trading_system.registered_market_event_monitor import RegisteredMarketEventMonitor
from trading_system.tracked_candle_pipeline import TrackedMarketCandle
from trading_system.tracked_etoro_live import TrackedEtoroMarketUpdate
from trading_system.tracked_event_reaction_live import TrackedRuntimeMarketBatch
from trading_system.tracked_instrument_etoro import TrackedEtoroInstrument


TRACKED = TrackedEtoroInstrument(
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="Lse",
    etoro_instrument_id=123,
    etoro_symbol="ABC.L",
    etoro_display_name="ABC plc",
)
EVENT = MarketEvent(
    event_id="news-1",
    tracked_instrument_id="tracked-1",
    instrument="ABC",
    market="LSE",
    event_at=datetime(2026, 8, 23, 7, 5, 30, tzinfo=UTC),
    source=MarketEventSource.NEWS,
    kind=MarketEventKind.NEWS,
    title="Unexpected news",
)


def _batch(*, tracked_id: str = "tracked-1", candles: bool = True) -> TrackedRuntimeMarketBatch:
    update = TrackedEtoroMarketUpdate(
        tracked_instrument_id=tracked_id,
        instrument="ABC",
        market="Lse",
        etoro_instrument_id=123,
        update=EtoroMarketUpdate(
            instrument_id=123,
            bid=Decimal("99"),
            ask=Decimal("100"),
            last_execution=Decimal("99.5"),
            timestamp=datetime(2026, 8, 23, 7, 7, tzinfo=UTC),
            is_market_open=True,
            is_exchange_open=True,
            message_type="rate",
        ),
    )
    closed = ()
    if candles:
        closed = (
            TrackedMarketCandle(
                tracked_instrument_id=tracked_id,
                instrument="ABC",
                market="Lse",
                etoro_instrument_id=123,
                interval_minutes=1,
                start=datetime(2026, 8, 23, 7, 6, tzinfo=UTC),
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("97"),
                close=Decimal("97"),
                source_minutes=1,
            ),
        )
    return TrackedRuntimeMarketBatch(update=update, candles=closed)


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.results: dict[str, object | None] = {}

    def observe_event(self, **kwargs: object) -> object | None:
        self.calls.append(kwargs)
        event = kwargs["event"]
        assert isinstance(event, MarketEvent)
        return self.results.get(event.event_id)


class RegisteredMarketEventMonitorTests(unittest.TestCase):
    def test_registered_event_observes_matching_live_batch(self) -> None:
        runtime = _FakeRuntime()
        observation = object()
        runtime.results[EVENT.event_id] = observation
        monitor = RegisteredMarketEventMonitor(runtime)  # type: ignore[arg-type]
        monitor.register(EVENT, TRACKED)
        observed_at = datetime(2026, 8, 23, 7, 7, tzinfo=UTC)
        batch = _batch()

        results = monitor.observe_batch(batch, observed_at=observed_at)

        self.assertEqual(len(runtime.calls), 1)
        self.assertIs(runtime.calls[0]["reaction_candles"], batch.candles)
        self.assertEqual(runtime.calls[0]["observed_at"], observed_at)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].event, EVENT)
        self.assertIs(results[0].observation, observation)

    def test_nonmatching_tracked_batch_does_not_observe_event(self) -> None:
        runtime = _FakeRuntime()
        monitor = RegisteredMarketEventMonitor(runtime)  # type: ignore[arg-type]
        monitor.register(EVENT, TRACKED)

        results = monitor.observe_batch(
            _batch(tracked_id="tracked-2"),
            observed_at=datetime(2026, 8, 23, 7, 7, tzinfo=UTC),
        )

        self.assertEqual(results, ())
        self.assertEqual(runtime.calls, [])

    def test_empty_candle_batch_does_not_observe_events(self) -> None:
        runtime = _FakeRuntime()
        monitor = RegisteredMarketEventMonitor(runtime)  # type: ignore[arg-type]
        monitor.register(EVENT, TRACKED)

        self.assertEqual(
            monitor.observe_batch(
                _batch(candles=False),
                observed_at=datetime(2026, 8, 23, 7, 7, tzinfo=UTC),
            ),
            (),
        )
        self.assertEqual(runtime.calls, [])

    def test_registration_is_idempotent_but_conflicting_event_id_fails_closed(self) -> None:
        runtime = _FakeRuntime()
        monitor = RegisteredMarketEventMonitor(runtime)  # type: ignore[arg-type]
        monitor.register(EVENT, TRACKED)
        monitor.register(EVENT, TRACKED)
        changed = MarketEvent(
            event_id=EVENT.event_id,
            tracked_instrument_id=EVENT.tracked_instrument_id,
            instrument=EVENT.instrument,
            market=EVENT.market,
            event_at=EVENT.event_at,
            source=EVENT.source,
            kind=MarketEventKind.GUIDANCE,
            title="Changed event",
        )

        with self.assertRaisesRegex(ValueError, "registration changed"):
            monitor.register(changed, TRACKED)

    def test_registration_validates_event_and_tracked_identity_with_canonical_market(self) -> None:
        runtime = _FakeRuntime()
        monitor = RegisteredMarketEventMonitor(runtime)  # type: ignore[arg-type]
        monitor.register(EVENT, TRACKED)
        mismatch = TrackedEtoroInstrument(
            tracked_instrument_id="tracked-1",
            instrument="ABC",
            market="NASDAQ",
            etoro_instrument_id=123,
            etoro_symbol="ABC",
            etoro_display_name="ABC plc",
        )

        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            monitor.register(EVENT, mismatch)

    def test_multiple_events_for_same_instrument_are_observed_in_registration_order(self) -> None:
        runtime = _FakeRuntime()
        second = MarketEvent(
            event_id="news-2",
            tracked_instrument_id="tracked-1",
            instrument="ABC",
            market="LSE",
            event_at=datetime(2026, 8, 23, 7, 6, tzinfo=UTC),
            source=MarketEventSource.MANUAL,
            kind=MarketEventKind.CUSTOM,
            title="Manual event",
        )
        runtime.results[EVENT.event_id] = object()
        runtime.results[second.event_id] = object()
        monitor = RegisteredMarketEventMonitor(runtime)  # type: ignore[arg-type]
        monitor.register(EVENT, TRACKED)
        monitor.register(second, TRACKED)

        results = monitor.observe_batch(
            _batch(),
            observed_at=datetime(2026, 8, 23, 7, 7, tzinfo=UTC),
        )

        self.assertEqual([item.event.event_id for item in results], ["news-1", "news-2"])
        self.assertEqual(
            [call["event"].event_id for call in runtime.calls],  # type: ignore[union-attr]
            ["news-1", "news-2"],
        )

    def test_unregister_is_explicit_and_idempotent(self) -> None:
        runtime = _FakeRuntime()
        monitor = RegisteredMarketEventMonitor(runtime)  # type: ignore[arg-type]
        monitor.register(EVENT, TRACKED)

        self.assertTrue(monitor.unregister(" news-1 "))
        self.assertFalse(monitor.unregister("news-1"))
        with self.assertRaisesRegex(ValueError, "event_id must not be blank"):
            monitor.unregister("   ")

        monitor.observe_batch(
            _batch(),
            observed_at=datetime(2026, 8, 23, 7, 7, tzinfo=UTC),
        )
        self.assertEqual(runtime.calls, [])


if __name__ == "__main__":
    unittest.main()
