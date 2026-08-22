import unittest
import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient

from trading_system.api import create_app, MAX_CALENDAR_LOOKAHEAD_DAYS
from trading_system.calendar_provider import CalendarCandidate
from trading_system.calendar_repository import InMemoryCalendarEventRepository
from trading_system.event_repository import InMemoryEventExpectationRepository


def _candidate(**overrides) -> CalendarCandidate:
    defaults = dict(
        company_name="Apple Inc",
        instrument="AAPL",
        market="NASDAQ",
        event_type="earnings",
        # Relative to today, and well inside the API's default/max 30-day
        # lookahead window - GET /api/v1/calendar/upcoming with no explicit
        # from_date/to_date must still find this candidate.
        scheduled_date=date.today() + timedelta(days=10),
        source="finnhub",
        occurrence_key="2026Q4",
    )
    defaults.update(overrides)
    return CalendarCandidate(**defaults)


class CalendarApiTests(unittest.TestCase):
    READ_KEY = "test-read-api-key"
    ADMIN_KEY = "test-admin-token"
    CONTROL_KEY = "test-control-api-key"

    def setUp(self) -> None:
        self.calendar_repo = InMemoryCalendarEventRepository()
        self.client = TestClient(
            create_app(
                InMemoryEventExpectationRepository(),
                calendar_repository=self.calendar_repo,
                admin_token=self.ADMIN_KEY,
                read_api_key=self.READ_KEY,
                control_api_key=self.CONTROL_KEY,
            )
        )

    def _read_headers(self, key: str | None = None) -> dict[str, str]:
        return {"X-MarketAI-Key": key if key is not None else self.READ_KEY}

    def _control_headers(self, key: str | None = None) -> dict[str, str]:
        return {"X-MarketAI-Control-Key": key if key is not None else self.CONTROL_KEY}

    # -- GET /api/v1/calendar/upcoming: read auth ---------------------------

    def test_upcoming_requires_the_read_key(self) -> None:
        response = self.client.get("/api/v1/calendar/upcoming")
        self.assertEqual(response.status_code, 401)

    def test_upcoming_rejects_a_wrong_read_key(self) -> None:
        response = self.client.get(
            "/api/v1/calendar/upcoming", headers=self._read_headers("wrong-key")
        )
        self.assertEqual(response.status_code, 401)

    def test_upcoming_returns_synced_candidates(self) -> None:
        self.calendar_repo.sync_candidates([_candidate()], source="finnhub")

        response = self.client.get(
            "/api/v1/calendar/upcoming", headers=self._read_headers()
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["instrument"], "AAPL")
        self.assertEqual(body[0]["status"], "candidate")

    # -- calendar range MVP: default/max 30-day lookahead --------------------

    def test_upcoming_default_window_does_not_reach_beyond_30_days(self) -> None:
        # No explicit from_date/to_date: the default window must be capped
        # at MAX_CALENDAR_LOOKAHEAD_DAYS, not the old 180-day default - a
        # candidate further out must not appear.
        self.calendar_repo.sync_candidates(
            [_candidate(scheduled_date=date.today() + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS + 5))],
            source="finnhub",
        )

        response = self.client.get("/api/v1/calendar/upcoming", headers=self._read_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_upcoming_default_window_includes_exactly_30_days_out(self) -> None:
        self.calendar_repo.sync_candidates(
            [_candidate(scheduled_date=date.today() + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS))],
            source="finnhub",
        )

        response = self.client.get("/api/v1/calendar/upcoming", headers=self._read_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_upcoming_rejects_an_explicit_lookahead_beyond_30_days(self) -> None:
        response = self.client.get(
            "/api/v1/calendar/upcoming",
            params={
                "from_date": date.today().isoformat(),
                "to_date": (date.today() + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS + 1)).isoformat(),
            },
            headers=self._read_headers(),
        )

        self.assertEqual(response.status_code, 422)

    def test_upcoming_accepts_an_explicit_lookahead_of_exactly_30_days(self) -> None:
        response = self.client.get(
            "/api/v1/calendar/upcoming",
            params={
                "from_date": date.today().isoformat(),
                "to_date": (date.today() + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS)).isoformat(),
            },
            headers=self._read_headers(),
        )

        self.assertEqual(response.status_code, 200)

    def test_upcoming_never_accepts_the_admin_token_as_read_auth(self) -> None:
        response = self.client.get(
            "/api/v1/calendar/upcoming", headers={"X-MarketAI-Key": self.ADMIN_KEY}
        )
        self.assertEqual(response.status_code, 401)

    # -- POST .../track and .../untrack: write auth is the control key, ----
    # -- never the read key --------------------------------------------------

    def test_track_requires_the_control_key_not_the_read_key(self) -> None:
        inserted = self.calendar_repo.sync_candidates([_candidate()], source="finnhub").inserted[0]

        response = self.client.post(
            f"/api/v1/calendar/{inserted}/track", headers=self._read_headers()
        )

        self.assertEqual(response.status_code, 401)

    def test_track_with_no_auth_header_is_rejected(self) -> None:
        inserted = self.calendar_repo.sync_candidates([_candidate()], source="finnhub").inserted[0]

        response = self.client.post(f"/api/v1/calendar/{inserted}/track")

        self.assertEqual(response.status_code, 401)

    def test_track_with_the_control_key_promotes_a_candidate(self) -> None:
        inserted = self.calendar_repo.sync_candidates([_candidate()], source="finnhub").inserted[0]

        response = self.client.post(
            f"/api/v1/calendar/{inserted}/track", headers=self._control_headers()
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "tracked")

    def test_track_unknown_event_is_404(self) -> None:
        # A syntactically valid UUID that just doesn't match any row -
        # genuinely "not found", distinct from a malformed path segment
        # (see the malformed-UUID tests below).
        response = self.client.post(
            f"/api/v1/calendar/{uuid.uuid4()}/track", headers=self._control_headers()
        )
        self.assertEqual(response.status_code, 404)

    def test_untrack_unknown_event_is_404(self) -> None:
        response = self.client.post(
            f"/api/v1/calendar/{uuid.uuid4()}/untrack", headers=self._control_headers()
        )
        self.assertEqual(response.status_code, 404)

    # -- P2 regression: malformed calendar_event_id must never reach the ---
    # -- repository (Supabase RPC) or surface as a 500 ----------------------

    def test_track_with_a_malformed_uuid_is_a_clean_4xx_not_a_500(self) -> None:
        # calendar_events.id is a Postgres uuid column in production - an
        # arbitrary string must be rejected before it ever reaches the
        # repository, not surface as an unhandled 500.
        response = self.client.post(
            "/api/v1/calendar/does-not-exist/track", headers=self._control_headers()
        )
        self.assertIn(response.status_code, (400, 422))
        self.assertLess(response.status_code, 500)

    def test_untrack_with_a_malformed_uuid_is_a_clean_4xx_not_a_500(self) -> None:
        response = self.client.post(
            "/api/v1/calendar/does-not-exist/untrack", headers=self._control_headers()
        )
        self.assertIn(response.status_code, (400, 422))
        self.assertLess(response.status_code, 500)

    def test_track_and_untrack_reject_a_malformed_uuid_identically(self) -> None:
        track_response = self.client.post(
            "/api/v1/calendar/not-a-uuid/track", headers=self._control_headers()
        )
        untrack_response = self.client.post(
            "/api/v1/calendar/not-a-uuid/untrack", headers=self._control_headers()
        )
        self.assertEqual(track_response.status_code, untrack_response.status_code)

    def test_track_with_a_malformed_uuid_never_reaches_the_repository(self) -> None:
        # Behavioral proof, not just a status-code check: the repository's
        # track() must never even be called for a malformed id - a broken
        # repository (or one that would otherwise 500 on bad input) must
        # still yield a clean 4xx.
        original_track = self.calendar_repo.track

        def _track_should_not_be_called(*_args, **_kwargs):
            raise AssertionError("repository.track() must not be called for a malformed UUID")

        self.calendar_repo.track = _track_should_not_be_called
        try:
            response = self.client.post(
                "/api/v1/calendar/not-a-uuid/track", headers=self._control_headers()
            )
        finally:
            self.calendar_repo.track = original_track

        self.assertLess(response.status_code, 500)
        self.assertNotEqual(response.status_code, 200)

    def test_untrack_requires_the_control_key_not_the_read_key(self) -> None:
        inserted = self.calendar_repo.sync_candidates([_candidate()], source="finnhub").inserted[0]
        self.calendar_repo.track(inserted)

        response = self.client.post(
            f"/api/v1/calendar/{inserted}/untrack", headers=self._read_headers()
        )

        self.assertEqual(response.status_code, 401)

    def test_untrack_with_the_control_key_demotes_a_tracked_event(self) -> None:
        inserted = self.calendar_repo.sync_candidates([_candidate()], source="finnhub").inserted[0]
        self.calendar_repo.track(inserted)

        response = self.client.post(
            f"/api/v1/calendar/{inserted}/untrack", headers=self._control_headers()
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "candidate")

    # -- POST /api/v1/calendar/manual: admin-token only, backend/tooling ---

    def test_manual_event_requires_the_admin_token(self) -> None:
        response = self.client.post(
            "/api/v1/calendar/manual",
            json={
                "company_name": "Afarak Group",
                "instrument": "AFAGR.HE",
                "market": "Suomi",
                "event_type": "production_report",
                "scheduled_date": "2026-10-15",
                "source": "manual",
            },
            headers=self._control_headers(),
        )
        self.assertEqual(response.status_code, 401)

    def test_manual_event_with_admin_token_creates_a_non_earnings_candidate(self) -> None:
        response = self.client.post(
            "/api/v1/calendar/manual",
            json={
                "company_name": "Afarak Group",
                "instrument": "AFAGR.HE",
                "market": "Suomi",
                "event_type": "production_report",
                "scheduled_date": "2026-10-15",
                "source": "manual",
            },
            headers={"X-Admin-Token": self.ADMIN_KEY},
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["event_type"], "production_report")
        self.assertEqual(body["status"], "candidate")

    def test_manual_event_works_without_a_fiscal_quarter_occurrence_key(self) -> None:
        # A one-off production report has no notion of fiscal year/quarter -
        # the request must not need to supply occurrence_key at all.
        first_date = (date.today() + timedelta(days=5)).isoformat()
        second_date = (date.today() + timedelta(days=20)).isoformat()
        response = self.client.post(
            "/api/v1/calendar/manual",
            json={
                "company_name": "Afarak Group",
                "instrument": "AFAGR.HE",
                "market": "Suomi",
                "event_type": "production_report",
                "scheduled_date": first_date,
                "source": "manual",
            },
            headers={"X-Admin-Token": self.ADMIN_KEY},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["occurrence_key"], "manual")

        # Idempotent: re-posting the same manual event (still no
        # occurrence_key) must not create a second row.
        second = self.client.post(
            "/api/v1/calendar/manual",
            json={
                "company_name": "Afarak Group",
                "instrument": "AFAGR.HE",
                "market": "Suomi",
                "event_type": "production_report",
                "scheduled_date": second_date,
                "source": "manual",
            },
            headers={"X-Admin-Token": self.ADMIN_KEY},
        )
        self.assertEqual(second.status_code, 201)

        upcoming = self.client.get(
            "/api/v1/calendar/upcoming",
            params={
                "from_date": date.today().isoformat(),
                "to_date": (date.today() + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS)).isoformat(),
            },
            headers=self._read_headers(),
        ).json()
        self.assertEqual(len([e for e in upcoming if e["instrument"] == "AFAGR.HE"]), 1)

    # -- occurrence identity (P1 fix) surfaced through the API --------------

    def test_tracked_q3_does_not_prevent_q4_candidate_from_appearing_in_upcoming(self) -> None:
        q3_id = self.calendar_repo.sync_candidates(
            [_candidate(scheduled_date=date.today() + timedelta(days=5), occurrence_key="2026Q3")],
            source="finnhub",
        ).inserted[0]
        self.calendar_repo.track(q3_id)

        self.calendar_repo.sync_candidates(
            [_candidate(scheduled_date=date.today() + timedelta(days=25), occurrence_key="2026Q4")],
            source="finnhub",
        )

        response = self.client.get(
            "/api/v1/calendar/upcoming",
            params={
                "from_date": date.today().isoformat(),
                "to_date": (date.today() + timedelta(days=MAX_CALENDAR_LOOKAHEAD_DAYS)).isoformat(),
            },
            headers=self._read_headers(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        occurrence_keys = {(e["instrument"], e["occurrence_key"]): e["status"] for e in body}
        self.assertEqual(occurrence_keys[("AAPL", "2026Q3")], "tracked")
        self.assertEqual(occurrence_keys[("AAPL", "2026Q4")], "candidate")

    # -- service disabled (no read key configured) fails closed -------------

    def test_upcoming_fails_closed_when_read_key_not_configured(self) -> None:
        client = TestClient(
            create_app(
                InMemoryEventExpectationRepository(),
                calendar_repository=InMemoryCalendarEventRepository(),
                admin_token=self.ADMIN_KEY,
                control_api_key=self.CONTROL_KEY,
            )
        )
        response = client.get(
            "/api/v1/calendar/upcoming", headers={"X-MarketAI-Key": "anything"}
        )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
