import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from trading_system.tracking_profile_api import build_tracking_profile_router
from trading_system.tracking_profile_registry import (
    TrackedInstrumentProfileInstrumentNotFound,
    TrackedInstrumentProfileRecord,
)


class _Registry:
    def __init__(self) -> None:
        self.read_calls = []
        self.write_calls = []
        self.missing = False

    def list_for_instrument(self, tracked_instrument_id: str):
        self.read_calls.append(tracked_instrument_id)
        return [
            TrackedInstrumentProfileRecord(
                id="profile-1",
                tracked_instrument_id=tracked_instrument_id,
                profile_type="trend",
                specs="Watch relative strength",
                enabled=True,
                created_by="mobile",
                updated_by="mobile",
            )
        ]

    def upsert(self, **kwargs):
        self.write_calls.append(kwargs)
        if self.missing:
            raise TrackedInstrumentProfileInstrumentNotFound(
                kwargs["tracked_instrument_id"]
            )
        return TrackedInstrumentProfileRecord(
            id="profile-1",
            tracked_instrument_id=kwargs["tracked_instrument_id"],
            profile_type=kwargs["profile_type"],
            specs=kwargs["specs"],
            enabled=kwargs["enabled"],
            created_by=kwargs["actor"],
            updated_by=kwargs["actor"],
        )


class TrackingProfileApiTests(unittest.TestCase):
    def _client(self, registry: _Registry) -> TestClient:
        app = FastAPI()

        def require_read(value):
            if value != "read":
                raise HTTPException(status_code=401, detail="bad read key")

        def require_control(value):
            if value != "control":
                raise HTTPException(status_code=401, detail="bad control key")

        app.include_router(
            build_tracking_profile_router(
                require_read=require_read,
                require_control=require_control,
                get_tracking_profile_registry=lambda: registry,
            )
        )
        return TestClient(app)

    def test_get_uses_read_auth_and_only_reads_profile_registry(self) -> None:
        registry = _Registry()
        response = self._client(registry).get(
            "/api/v1/tracked-instruments/instrument-1/profiles",
            headers={"X-MarketAI-Key": "read"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(registry.read_calls, ["instrument-1"])
        self.assertEqual(registry.write_calls, [])
        self.assertEqual(response.json()[0]["profile_type"], "trend")

    def test_get_rejects_control_key_before_read(self) -> None:
        registry = _Registry()
        response = self._client(registry).get(
            "/api/v1/tracked-instruments/instrument-1/profiles",
            headers={"X-MarketAI-Control-Key": "control"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(registry.read_calls, [])

    def test_put_uses_control_auth_actor_and_profile_registry_only(self) -> None:
        registry = _Registry()
        response = self._client(registry).put(
            "/api/v1/tracked-instruments/instrument-1/profiles/future_tech",
            headers={
                "X-MarketAI-Control-Key": "control",
                "X-MarketAI-Actor": " mobile ",
            },
            json={"specs": " AI photonics, GaN, SiC ", "enabled": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(registry.read_calls, [])
        self.assertEqual(
            registry.write_calls,
            [
                {
                    "tracked_instrument_id": "instrument-1",
                    "profile_type": "future_tech",
                    "specs": "AI photonics, GaN, SiC",
                    "enabled": True,
                    "actor": "mobile",
                }
            ],
        )

    def test_put_rejects_read_key_before_write(self) -> None:
        registry = _Registry()
        response = self._client(registry).put(
            "/api/v1/tracked-instruments/instrument-1/profiles/trend",
            headers={
                "X-MarketAI-Key": "read",
                "X-MarketAI-Actor": "mobile",
            },
            json={"specs": "watch trend"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(registry.write_calls, [])

    def test_put_requires_explicit_actor_before_write(self) -> None:
        registry = _Registry()
        response = self._client(registry).put(
            "/api/v1/tracked-instruments/instrument-1/profiles/earnings",
            headers={"X-MarketAI-Control-Key": "control"},
            json={"specs": "watch earnings"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(registry.write_calls, [])

    def test_put_rejects_unknown_profile_type_before_write(self) -> None:
        registry = _Registry()
        response = self._client(registry).put(
            "/api/v1/tracked-instruments/instrument-1/profiles/news",
            headers={
                "X-MarketAI-Control-Key": "control",
                "X-MarketAI-Actor": "mobile",
            },
            json={"specs": "watch news"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(registry.write_calls, [])

    def test_put_maps_missing_canonical_instrument_to_404(self) -> None:
        registry = _Registry()
        registry.missing = True
        response = self._client(registry).put(
            "/api/v1/tracked-instruments/missing/profiles/trend",
            headers={
                "X-MarketAI-Control-Key": "control",
                "X-MarketAI-Actor": "mobile",
            },
            json={"specs": "watch trend"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(registry.write_calls), 1)


if __name__ == "__main__":
    unittest.main()
