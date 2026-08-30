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


class TrackedInstrumentApiTests(unittest.TestCase):
    def _client(self, registry, expected_key="control"):
        app = FastAPI()

        def require_control(value):
            if value != expected_key:
                raise HTTPException(status_code=401, detail="bad control key")

        app.include_router(
            build_tracked_instrument_router(
                require_control=require_control,
                get_tracked_instrument_registry=lambda: registry,
            )
        )
        return TestClient(app)

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
