import unittest

from trading_system.etoro_instrument_resolver import ResolvedEtoroInstrument
from trading_system.tracked_instrument_registry import TrackedInstrumentRecord
from trading_system.tracking_profile_registry import TrackedInstrumentProfileRecord
from trading_system.trend_monitoring_targets import select_trend_monitoring_targets


class _TrackedReader:
    def __init__(self, records):
        self.records = records

    def list_active(self):
        return list(self.records)


class _ProfileReader:
    def __init__(self, profiles):
        self.profiles = profiles
        self.requested_ids = None

    def list_for_instruments(self, tracked_instrument_ids):
        self.requested_ids = list(tracked_instrument_ids)
        return self.profiles


class _Resolver:
    def __init__(self, resolved_by_symbol):
        self.resolved_by_symbol = resolved_by_symbol
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return self.resolved_by_symbol.get(request.instrument)


def record(identifier, instrument, *, active=True):
    return TrackedInstrumentRecord(
        id=identifier,
        instrument=instrument,
        market="USA",
        company_name=f"{instrument} Inc",
        sources=("manual",),
        active=active,
        created_by="test",
        updated_by="test",
    )


def profile(identifier, *, enabled):
    return TrackedInstrumentProfileRecord(
        id=f"profile-{identifier}",
        tracked_instrument_id=identifier,
        profile_type="trend",
        specs="",
        enabled=enabled,
        created_by="test",
        updated_by="test",
    )


class TrendMonitoringTargetsTests(unittest.TestCase):
    def test_only_enabled_trend_profiles_are_resolved(self):
        records = [record("a", "AAA"), record("b", "BBB")]
        profiles = {"a": [profile("a", enabled=True)], "b": [profile("b", enabled=False)]}
        resolver = _Resolver(
            {
                "AAA": ResolvedEtoroInstrument(
                    instrument_id=101,
                    symbol="AAA",
                    display_name="AAA Inc",
                    market="NASDAQ",
                )
            }
        )

        selected = select_trend_monitoring_targets(
            _TrackedReader(records), _ProfileReader(profiles), resolver
        )

        self.assertEqual([item.tracked_instrument_id for item in selected.resolved], ["a"])
        self.assertEqual([request.instrument for request in resolver.requests], ["AAA"])
        self.assertEqual(selected.unresolved_tracked_instrument_ids, ())

    def test_unresolved_enabled_target_is_explicit_and_not_guessed(self):
        records = [record("a", "AAA")]
        selected = select_trend_monitoring_targets(
            _TrackedReader(records),
            _ProfileReader({"a": [profile("a", enabled=True)]}),
            _Resolver({}),
        )
        self.assertEqual(selected.resolved, ())
        self.assertEqual(selected.unresolved_tracked_instrument_ids, ("a",))

    def test_no_active_instruments_does_not_read_profiles_or_resolve(self):
        profiles = _ProfileReader({})
        resolver = _Resolver({})
        selected = select_trend_monitoring_targets(_TrackedReader([]), profiles, resolver)
        self.assertEqual(selected.resolved, ())
        self.assertIsNone(profiles.requested_ids)
        self.assertEqual(resolver.requests, [])

    def test_profile_batch_must_match_active_identity_set(self):
        with self.assertRaisesRegex(RuntimeError, "did not match"):
            select_trend_monitoring_targets(
                _TrackedReader([record("a", "AAA")]),
                _ProfileReader({}),
                _Resolver({}),
            )

    def test_inactive_record_from_active_reader_fails_closed_before_monitoring(self):
        with self.assertRaisesRegex(RuntimeError, "inactive"):
            select_trend_monitoring_targets(
                _TrackedReader([record("a", "AAA", active=False)]),
                _ProfileReader({"a": [profile("a", enabled=True)]}),
                _Resolver({}),
            )

    def test_duplicate_resolved_etoro_identity_fails_closed(self):
        records = [record("a", "AAA"), record("b", "BBB")]
        profiles = {"a": [profile("a", enabled=True)], "b": [profile("b", enabled=True)]}
        same = ResolvedEtoroInstrument(
            instrument_id=101,
            symbol="AAA",
            display_name="Shared",
            market="NASDAQ",
        )
        with self.assertRaisesRegex(RuntimeError, "duplicate resolved eToro"):
            select_trend_monitoring_targets(
                _TrackedReader(records),
                _ProfileReader(profiles),
                _Resolver({"AAA": same, "BBB": same}),
            )

    def test_duplicate_trend_profile_rows_fail_closed(self):
        records = [record("a", "AAA")]
        with self.assertRaisesRegex(RuntimeError, "multiple trend profiles"):
            select_trend_monitoring_targets(
                _TrackedReader(records),
                _ProfileReader({"a": [profile("a", enabled=True), profile("a", enabled=False)]}),
                _Resolver({}),
            )


if __name__ == "__main__":
    unittest.main()
