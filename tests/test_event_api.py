import unittest
from datetime import date

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.event_repository import InMemoryEventExpectationRepository
from trading_system.models import EventExpectation


class FakePaperRepository:
    def __init__(self, run=None) -> None:
        self.run = run

    def get_latest_for_event(self, event_id: str):
        return self.run


class EventApiTests(unittest.TestCase):
    READ_KEY = "test-read-api-key"
    ADMIN_KEY = "test-admin-token"

    def setUp(self) -> None:
        event = EventExpectation(
            event_id="hays-fy2026-results",
            instrument="HAS.L",
            event_name="Hays plc FY2026 results",
            scheduled_date=date(2026, 8, 20),
            consensus={"fy27_operating_profit_pre_exceptional_gbp_m": 55.6},
            triggers={"bull_fy27_operating_profit_gbp_m": 60.0},
            source_name="Hays plc analyst consensus",
            source_as_of=date(2026, 7, 1),
            version=1,
        )
        self.repo = InMemoryEventExpectationRepository(events={event.event_id: event})
        self.paper_repo = FakePaperRepository()
        self.client = TestClient(
            create_app(
                self.repo,
                paper_repository=self.paper_repo,
                admin_token=self.ADMIN_KEY,
                read_api_key=self.READ_KEY,
            )
        )

    def _read_headers(self, key: str | None = None) -> dict[str, str]:
        return {"X-MarketAI-Key": key if key is not None else self.READ_KEY}

    # -- read auth: GET /api/v1/events/{event_id} -----------------------

    def test_get_event_returns_current_version(self) -> None:
        response = self.client.get(
            "/api/v1/events/hays-fy2026-results", headers=self._read_headers()
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["instrument"], "HAS.L")
        self.assertEqual(body["version"], 1)
        self.assertEqual(
            body["consensus"]["fy27_operating_profit_pre_exceptional_gbp_m"],
            55.6,
        )

    def test_get_event_without_read_key_is_denied(self) -> None:
        response = self.client.get("/api/v1/events/hays-fy2026-results")

        self.assertEqual(response.status_code, 401)
        self.assertNotIn(self.READ_KEY, response.text)

    def test_get_event_with_wrong_read_key_is_denied(self) -> None:
        response = self.client.get(
            "/api/v1/events/hays-fy2026-results",
            headers=self._read_headers("not-the-right-key"),
        )

        self.assertEqual(response.status_code, 401)

    def test_admin_token_does_not_work_as_a_read_key(self) -> None:
        """The admin token must not double as read credentials unless that is
        an explicit design decision - it currently is not one."""
        response = self.client.get(
            "/api/v1/events/hays-fy2026-results",
            headers=self._read_headers(self.ADMIN_KEY),
        )

        self.assertEqual(response.status_code, 401)

    def test_read_key_in_query_string_is_not_accepted(self) -> None:
        """Only the header carries the token; a query param must not work."""
        response = self.client.get(
            f"/api/v1/events/hays-fy2026-results?x_marketai_key={self.READ_KEY}"
        )

        self.assertEqual(response.status_code, 401)

    # -- read auth: GET /api/v1/events -----------------------------------

    def test_list_events_requires_read_key(self) -> None:
        denied = self.client.get("/api/v1/events")
        self.assertEqual(denied.status_code, 401)

        allowed = self.client.get("/api/v1/events", headers=self._read_headers())
        self.assertEqual(allowed.status_code, 200)

    # -- read auth: GET /api/v1/events/{event_id}/paper-status -----------

    def test_paper_status_returns_clean_pre_event_state(self) -> None:
        response = self.client.get(
            "/api/v1/events/hays-fy2026-results/paper-status",
            headers=self._read_headers(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["trading_mode"], "PAPER")
        self.assertEqual(body["instrument"], "HAS.L")
        self.assertIsNone(body["paper_run"])

    def test_paper_status_exposes_latest_persisted_decision(self) -> None:
        self.paper_repo.run = {
            "status": "waiting_confirmation",
            "message": "technical confirmation is not aligned",
            "strategy": {"direction": "LONG", "confidence": 63},
            "risk": None,
            "paper_order": None,
        }

        response = self.client.get(
            "/api/v1/events/hays-fy2026-results/paper-status",
            headers=self._read_headers(),
        )

        self.assertEqual(response.status_code, 200)
        run = response.json()["paper_run"]
        self.assertEqual(run["status"], "waiting_confirmation")
        self.assertEqual(run["strategy"]["direction"], "LONG")
        self.assertIsNone(run["paper_order"])

    def test_paper_status_without_read_key_is_denied(self) -> None:
        response = self.client.get("/api/v1/events/hays-fy2026-results/paper-status")

        self.assertEqual(response.status_code, 401)

    def test_paper_status_with_admin_token_instead_of_read_key_is_denied(self) -> None:
        response = self.client.get(
            "/api/v1/events/hays-fy2026-results/paper-status",
            headers=self._read_headers(self.ADMIN_KEY),
        )

        self.assertEqual(response.status_code, 401)

    # -- read auth: unset MARKETAI_READ_API_KEY fails closed --------------

    def test_unset_read_api_key_fails_closed_instead_of_being_open(self) -> None:
        client = TestClient(
            create_app(
                self.repo,
                paper_repository=self.paper_repo,
                admin_token=self.ADMIN_KEY,
                read_api_key=None,
            )
        )

        response = client.get(
            "/api/v1/events/hays-fy2026-results", headers=self._read_headers()
        )

        self.assertEqual(response.status_code, 503)

    # -- /health stays open and reveals nothing --------------------------

    def test_health_works_without_any_token(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body, {"status": "ok", "trading_mode": "PAPER"})
        self.assertNotIn(self.READ_KEY, response.text)
        self.assertNotIn(self.ADMIN_KEY, response.text)

    # -- admin write endpoint: unchanged, no regression --------------------

    def test_write_requires_admin_token(self) -> None:
        response = self.client.post(
            "/api/v1/events/hays-fy2026-results/expectation-versions",
            json={"change_note": "raise bull threshold", "triggers": {}},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)

    def test_read_key_does_not_work_as_admin_token(self) -> None:
        response = self.client.post(
            "/api/v1/events/hays-fy2026-results/expectation-versions",
            headers={"X-Admin-Token": self.READ_KEY},
            json={"change_note": "raise bull threshold", "triggers": {}},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)

    def test_authorized_write_creates_next_version_and_merges_unchanged_fields(self) -> None:
        response = self.client.post(
            "/api/v1/events/hays-fy2026-results/expectation-versions",
            headers={"X-Admin-Token": self.ADMIN_KEY},
            json={
                "change_note": "raise bull threshold after updated research",
                "triggers": {"bull_fy27_operating_profit_gbp_m": 62.0},
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["version"], 2)
        self.assertEqual(body["triggers"]["bull_fy27_operating_profit_gbp_m"], 62.0)
        self.assertEqual(
            body["consensus"]["fy27_operating_profit_pre_exceptional_gbp_m"],
            55.6,
        )

    def test_change_note_is_required(self) -> None:
        response = self.client.post(
            "/api/v1/events/hays-fy2026-results/expectation-versions",
            headers={"X-Admin-Token": self.ADMIN_KEY},
            json={"triggers": {"bull_fy27_operating_profit_gbp_m": 62.0}},
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
