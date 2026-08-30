from types import SimpleNamespace

import pytest

from trading_system.tracking_profile_registry import (
    SupabaseTrackedInstrumentProfileRegistry,
    TrackedInstrumentProfileInstrumentNotFound,
)


class _Query:
    def __init__(self, response, calls: list[tuple]) -> None:
        self.response = response
        self.calls = calls

    def select(self, value):
        self.calls.append(("select", value))
        return self

    def eq(self, key, value):
        self.calls.append(("eq", key, value))
        return self

    def order(self, key):
        self.calls.append(("order", key))
        return self

    def execute(self):
        self.calls.append(("execute",))
        return self.response


class _RpcCall:
    def __init__(self, response) -> None:
        self.response = response

    def execute(self):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _Client:
    def __init__(
        self,
        *,
        instrument_response=None,
        profile_response=None,
        rpc_response=None,
    ) -> None:
        self.instrument_response = instrument_response
        self.profile_response = profile_response
        self.rpc_response = rpc_response
        self.table_calls: list[tuple] = []
        self.rpc_calls: list[tuple[str, dict]] = []

    def table(self, name: str):
        self.table_calls.append(("table", name))
        if name == "tracked_instruments":
            return _Query(self.instrument_response, self.table_calls)
        if name == "tracked_instrument_profiles":
            return _Query(self.profile_response, self.table_calls)
        raise AssertionError(f"unexpected table: {name}")

    def rpc(self, name: str, params: dict):
        self.rpc_calls.append((name, params))
        return _RpcCall(self.rpc_response)


def _row(**overrides):
    row = {
        "id": "profile-1",
        "tracked_instrument_id": "instrument-1",
        "profile_type": "earnings",
        "specs": "Track guidance and margin changes",
        "enabled": True,
        "created_by": "user",
        "updated_by": "user",
        "created_at": "2026-08-30T12:00:00+00:00",
        "updated_at": "2026-08-30T12:00:00+00:00",
    }
    row.update(overrides)
    return row


def test_list_for_instrument_verifies_identity_then_reads_matching_profiles() -> None:
    client = _Client(
        instrument_response=SimpleNamespace(data=[{"id": "instrument-1"}]),
        profile_response=SimpleNamespace(data=[_row()]),
    )
    registry = SupabaseTrackedInstrumentProfileRegistry(client)

    records = registry.list_for_instrument(" instrument-1 ")

    assert len(records) == 1
    assert records[0].profile_type == "earnings"
    assert records[0].specs == "Track guidance and margin changes"
    assert client.table_calls == [
        ("table", "tracked_instruments"),
        ("select", "id"),
        ("eq", "id", "instrument-1"),
        ("execute",),
        ("table", "tracked_instrument_profiles"),
        ("select", "*"),
        ("eq", "tracked_instrument_id", "instrument-1"),
        ("order", "profile_type"),
        ("execute",),
    ]


def test_list_for_instrument_maps_missing_canonical_identity_to_specific_error() -> None:
    client = _Client(instrument_response=SimpleNamespace(data=[]))
    registry = SupabaseTrackedInstrumentProfileRegistry(client)

    with pytest.raises(TrackedInstrumentProfileInstrumentNotFound):
        registry.list_for_instrument("missing")

    assert client.table_calls == [
        ("table", "tracked_instruments"),
        ("select", "id"),
        ("eq", "id", "missing"),
        ("execute",),
    ]


def test_list_for_instrument_rejects_blank_identity() -> None:
    registry = SupabaseTrackedInstrumentProfileRegistry(_Client())

    with pytest.raises(ValueError, match="tracked_instrument_id is required"):
        registry.list_for_instrument("   ")


def test_upsert_uses_only_canonical_profile_rpc() -> None:
    client = _Client(rpc_response=SimpleNamespace(data=_row(enabled=False)))
    registry = SupabaseTrackedInstrumentProfileRegistry(client)

    saved = registry.upsert(
        tracked_instrument_id="instrument-1",
        profile_type="earnings",
        specs="  Watch EPS surprise  ",
        enabled=False,
        actor="mobile-user",
    )

    assert saved.enabled is False
    assert client.rpc_calls == [
        (
            "upsert_tracked_instrument_profile",
            {
                "input_tracked_instrument_id": "instrument-1",
                "input_profile_type": "earnings",
                "input_specs": "  Watch EPS surprise  ",
                "input_enabled": False,
                "input_actor": "mobile-user",
            },
        )
    ]


def test_upsert_maps_missing_instrument_to_specific_error() -> None:
    client = _Client(
        rpc_response=RuntimeError("tracked_profile_instrument_not_found")
    )
    registry = SupabaseTrackedInstrumentProfileRegistry(client)

    with pytest.raises(TrackedInstrumentProfileInstrumentNotFound):
        registry.upsert(
            tracked_instrument_id="missing",
            profile_type="trend",
            specs="",
            enabled=True,
            actor="user",
        )


def test_invalid_identity_read_payload_fails_closed() -> None:
    client = _Client(
        instrument_response=SimpleNamespace(data={"unexpected": "shape"})
    )
    registry = SupabaseTrackedInstrumentProfileRegistry(client)

    with pytest.raises(RuntimeError, match="identity read returned invalid data"):
        registry.list_for_instrument("instrument-1")


def test_invalid_profile_read_payload_fails_closed() -> None:
    client = _Client(
        instrument_response=SimpleNamespace(data=[{"id": "instrument-1"}]),
        profile_response=SimpleNamespace(data={"unexpected": "shape"}),
    )
    registry = SupabaseTrackedInstrumentProfileRegistry(client)

    with pytest.raises(RuntimeError, match="profiles read returned invalid data"):
        registry.list_for_instrument("instrument-1")


def test_empty_upsert_response_fails_closed() -> None:
    client = _Client(rpc_response=SimpleNamespace(data=[]))
    registry = SupabaseTrackedInstrumentProfileRegistry(client)

    with pytest.raises(RuntimeError, match="returned no row"):
        registry.upsert(
            tracked_instrument_id="instrument-1",
            profile_type="future_tech",
            specs="",
            enabled=True,
            actor="user",
        )
