import unittest

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.tracked_instrument_registry import TrackedInstrumentRecord


class _FakeTrackedInstrumentRegistry:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def upsert(
        self,
        *,
        instrument: str,
        company_name: str,
        market: str,
        source: str,
        actor: str,
    ) -> TrackedInstrumentRecord:
        self.calls.append(
            {
                "instrument": instrument,
                "company_name": company_name,
                "market": market,
                "source": source,
                "actor": actor,
            }
        )
        return TrackedInstrumentRecord(
            id="instrument-1",
            instrument=instrument,
            market=market,
            company_name=company_name,
            sources=(source,),
            active=True,
        )


class TrackedInstrumentApiWiringTests(unittest.TestCase):
    def test_create_app_wires_canonical_control_endpoint(self) -> None:
        registry = _FakeTrackedInstrumentRegistry()
        client = TestClient(
            create_app(
                tracked_instrument_registry=registry,
                control_api_key="control-secret",
            )
        )

        response = client.post(
            "/api/v1/tracked-instruments",
            headers={
                "X-MarketAI-Control-Key": "control-secret",
                "X-MarketAI-Actor": "mobile-scanner",
            },
            json={
                "instrument": " nokia.he ",
                "company_name": " Nokia Oyj ",
                "market": " Helsinki ",
                "source": "scanner",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            registry.calls,
            [
                {
                    "instrument": "nokia.he",
                    "company_name": "Nokia Oyj",
                    "market": "Helsinki",
                    "source": "scanner",
                    "actor": "mobile-scanner",
                }
            ],
        )

    def test_create_app_keeps_read_key_out_of_tracking_write_auth(self) -> None:
        registry = _FakeTrackedInstrumentRegistry()
        client = TestClient(
            create_app(
                tracked_instrument_registry=registry,
                read_api_key="read-secret",
                control_api_key="control-secret",
            )
        )

        response = client.post(
            "/api/v1/tracked-instruments",
            headers={
                "X-MarketAI-Key": "read-secret",
                "X-MarketAI-Actor": "mobile-scanner",
            },
            json={
                "instrument": "NOKIA.HE",
                "company_name": "Nokia Oyj",
                "market": "Helsinki",
                "source": "scanner",
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(registry.calls, [])


if __name__ == "__main__":
    unittest.main()
