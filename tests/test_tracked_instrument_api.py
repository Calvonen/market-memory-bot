import unittest
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from trading_system.tracked_instrument_api import build_tracked_instrument_router
from trading_system.tracked_instrument_registry import (
    SupabaseTrackedInstrumentRegistry,
    TrackedInstrumentRecord,
)


class _Registry:
    def __init__(self) -> None:
        self.calls = []
        self.read_calls = 0

    def list_active(self):
        self.read_calls += 1
        return [
            TrackedInstrumentRecord(
                id="abc123",
                instrument="AIXA.DE",
                market="XETRA",
                company_name="Aixtron",
                sources=("scanner", "calendar"),
                active=True,
                created_by="mobile",
                updated_by="mobile",
            )
        ]

    def upsert(self, **kwargs):
        self.calls.append(kwargs)
        return TrackedInstrumentRecord(
            id="abc123",
            instrument=kwargs["instrument"],
            market=kwargs["market"],
            company_name=kwargs["company_name"],
            sources=(kwargs["source"],),
            active=True,
            created_by=kwargs["actor"],
            updated_by=kwargs["actor"],
        )


class _RpcClient:
    def __init__(self) -> None:
        self.calls = []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return SimpleNamespace(
            execute=lambda: SimpleNamespace(
                data={
                    "id": "abc123",
                    "instrument": "AIXA.DE",
                    "market": "XETRA",
                    "company_name": "Aixtron",
                    "sources": ["scanner"],
                    "active": True,
                    "created_by": "mobile",
                    "updated_by": "mobile",
                }
            )
        )


class _ReadQuery:
    def __init__(self, calls) -> None:
        self.calls = calls

    def select(self, value):
        self.calls.append(("select", value))
        return self

    def eq(self, column, value):
        self.calls.append(("eq", column, value))
        return self

    def order(self, column):
        self.calls.append(("order", column))
        return self

    def execute(self):
        self.calls.append(("execute",))
        return SimpleNamespace(
            data=[
                {
                    "id": "abc123",
                    "instrument": "AIXA.DE",
                    "market": "XETRA",
                    "company_name": "Aixtron",
                    "sources": ["scanner", "calendar"],
                    "active": True,
                    "created_by": "mobile",
                    "updated_by": "mobile",
                }
            ]
        )


class _ReadClient:
    def __init__(self) -> None:
        self.calls = []

    def table(self, name):
        self.calls.append(("table", name))
        return _ReadQuery(self.calls)


class TrackedInstrumentApiTests(unittest.TestCase):
    def _client(self, registry, expected_key="control", expected_read="read"):
        app = FastAPI()

        def require_control(value):
            if value != expected_key:
                raise HTTPException(status_code=401, detail="bad control key")

        def require_read(value):
            if value != expected_read:
                raise HTTPException(status_code=401, detail="bad read key")

        app.include_router(
            build_tracked_instrument_router(
                require_control=require_control,
                require_read=require_read,
                get_tracked_instrument_registry=lambda: registry,
            )
        )
        return TestClient(app)

    def test_get_uses_read_auth_and_returns_active_registry_records(self) -> None:
        registry = _Registry()
        response = self._client(registry).get(
            "/api/v1/tracked-instruments",
            headers={"X-MarketAI-Key": "read"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(registry.read_calls, 1)
        self.assertEqual(registry.calls, [])
        self.assertEqual(response.json()[0]["instrument"], "AIXA.DE")
        self.assertEqual(response.json()[0]["sources"], ["scanner", "calendar"])

    def test_get_rejects_control_or_missing_read_key_before_registry_call(self) -> None:
        registry = _Registry()
        response = self._client(registry).get(
            "/api/v1/tracked-instruments",
            headers={"X-MarketAI-Control-Key": "control"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(registry.read_calls, 0)

    def test_post_uses_control_auth_actor_and_canonical_registry_only(self) -> None:
        registry = _Registry()
        response = self._client(registry).post(
            "/api/v1/tracked-instruments",
            headers={
                "X-MarketAI-Control-Key": "control",
                "X-MarketAI-Actor": " mobile ",
            },
            json={
                "instrument": " AIXA.DE ",
                "company_name": " Aixtron ",
                "market": " XETRA ",
                "source": "scanner",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            registry.calls,
            [
                {
                    "instrument": "AIXA.DE",
                    "company_name": "Aixtron",
                    "market": "XETRA",
                    "source": "scanner",
                    "actor": "mobile",
                }
            ],
        )
        self.assertEqual(response.json()["sources"], ["scanner"])

    def test_post_rejects_missing_actor_before_registry_call(self) -> None:
        registry = _Registry()
        response = self._client(registry).post(
            "/api/v1/tracked-instruments",
            headers={"X-MarketAI-Control-Key": "control"},
            json={"instrument": "AIXA.DE", "source": "scanner"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(registry.calls, [])

    def test_post_rejects_read_or_missing_control_key(self) -> None:
        registry = _Registry()
        response = self._client(registry).post(
            "/api/v1/tracked-instruments",
            headers={
                "X-MarketAI-Key": "read",
                "X-MarketAI-Actor": "mobile",
            },
            json={"instrument": "AIXA.DE", "source": "scanner"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(registry.calls, [])

    def test_repository_reads_only_active_canonical_registry_rows(self) -> None:
        client = _ReadClient()
        records = SupabaseTrackedInstrumentRegistry(client).list_active()
        self.assertEqual([record.instrument for record in records], ["AIXA.DE"])
        self.assertEqual(
            client.calls,
            [
                ("table", "tracked_instruments"),
                ("select", "*"),
                ("eq", "active", True),
                ("order", "instrument"),
                ("order", "market"),
                ("execute",),
            ],
        )

    def test_repository_calls_only_canonical_registry_rpc(self) -> None:
        client = _RpcClient()
        record = SupabaseTrackedInstrumentRegistry(client).upsert(
            instrument="AIXA.DE",
            company_name="Aixtron",
            market="XETRA",
            source="scanner",
            actor="mobile",
        )
        self.assertEqual(record.id, "abc123")
        self.assertEqual(
            client.calls,
            [
                (
                    "upsert_tracked_instrument",
                    {
                        "input_instrument": "AIXA.DE",
                        "input_company_name": "Aixtron",
                        "input_market": "XETRA",
                        "input_source": "scanner",
                        "input_actor": "mobile",
                    },
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
