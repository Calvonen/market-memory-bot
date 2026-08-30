import unittest

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.tracking_profile_registry import TrackedInstrumentProfileRecord


class _ProfileRegistry:
    def __init__(self) -> None:
        self.read_calls: list[str] = []
        self.write_calls: list[dict] = []

    def list_for_instrument(self, tracked_instrument_id: str):
        self.read_calls.append(tracked_instrument_id)
        return []

    def upsert(self, **kwargs):
        self.write_calls.append(kwargs)
        return TrackedInstrumentProfileRecord(
            id="profile-1",
            tracked_instrument_id=kwargs["tracked_instrument_id"],
            profile_type=kwargs["profile_type"],
            specs=kwargs["specs"],
            enabled=kwargs["enabled"],
            created_by=kwargs["actor"],
            updated_by=kwargs["actor"],
        )


class TrackingProfileAppWiringTests(unittest.TestCase):
    def _client(self, registry: _ProfileRegistry) -> TestClient:
        return TestClient(
            create_app(
                tracking_profile_registry=registry,
                read_api_key="read",
                control_api_key="control",
            )
        )

    def test_create_app_mounts_profile_get_with_read_auth(self) -> None:
        registry = _ProfileRegistry()
        response = self._client(registry).get(
            "/api/v1/tracked-instruments/instrument-1/profiles",
            headers={"X-MarketAI-Key": "read"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(registry.read_calls, ["instrument-1"])
        self.assertEqual(registry.write_calls, [])

    def test_create_app_mounts_profile_put_with_control_auth(self) -> None:
        registry = _ProfileRegistry()
        response = self._client(registry).put(
            "/api/v1/tracked-instruments/instrument-1/profiles/trend",
            headers={
                "X-MarketAI-Control-Key": "control",
                "X-MarketAI-Actor": "mobile",
            },
            json={"specs": "Watch relative strength", "enabled": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile_type"], "trend")
        self.assertEqual(
            registry.write_calls,
            [
                {
                    "tracked_instrument_id": "instrument-1",
                    "profile_type": "trend",
                    "specs": "Watch relative strength",
                    "enabled": True,
                    "actor": "mobile",
                }
            ],
        )

    def test_profile_routes_keep_read_and_control_credentials_separate(self) -> None:
        registry = _ProfileRegistry()
        get_response = self._client(registry).get(
            "/api/v1/tracked-instruments/instrument-1/profiles",
            headers={"X-MarketAI-Control-Key": "control"},
        )
        put_response = self._client(registry).put(
            "/api/v1/tracked-instruments/instrument-1/profiles/trend",
            headers={
                "X-MarketAI-Key": "read",
                "X-MarketAI-Actor": "mobile",
            },
            json={"specs": "Watch relative strength"},
        )

        self.assertEqual(get_response.status_code, 401)
        self.assertEqual(put_response.status_code, 401)
        self.assertEqual(registry.read_calls, [])
        self.assertEqual(registry.write_calls, [])


if __name__ == "__main__":
    unittest.main()
