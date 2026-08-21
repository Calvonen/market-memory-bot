import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from fastapi.testclient import TestClient

from trading_system.api import create_app
from trading_system.event_repository import InMemoryEventExpectationRepository
from trading_system.models import EventExpectation
from trading_system.strategy_draft_repository import InMemoryStrategyDraftApprovalRepository


class FakePaperRepository:
    def __init__(self, run=None) -> None:
        self.run = run
        self.calls = 0

    def get_latest_for_event(self, event_id: str):
        self.calls += 1
        return self.run


def _valid_draft_body(**overrides) -> dict:
    body = {
        "instrument": "HAS.L",
        "event_name": "Hays plc FY2026 results",
        "scheduled_date": "2026-08-20",
        "consensus": {"fy27_operating_profit_pre_exceptional_gbp_m": 55.6},
        "important_kpis": ["fy27_operating_profit_pre_exceptional_gbp_m"],
        "bull_case": ["Fees grow above consensus"],
        "base_case": ["In-line results"],
        "bear_case": ["Margin compression"],
        "triggers": {"bull_fy27_operating_profit_gbp_m": 62.0},
        "invalidation_conditions": ["Guidance withdrawn"],
        "source_name": "Hays plc analyst consensus",
        "source_url": "https://example.com/consensus",
        "source_as_of": "2026-07-15",
        "change_note": "Raise bull threshold after updated research",
        "summary": "Hays FY26 results strategy draft",
        "assumptions": ["Fee income trend continues"],
        "unresolved_questions": [],
    }
    body.update(overrides)
    return body


class StrategyDraftApiTests(unittest.TestCase):
    READ_KEY = "test-read-api-key"
    ADMIN_KEY = "test-admin-token"
    CONTROL_KEY = "test-control-api-key"

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
        self.approval_repo = InMemoryStrategyDraftApprovalRepository(expectations=self.repo)
        self.client = TestClient(
            create_app(
                self.repo,
                paper_repository=self.paper_repo,
                approval_repository=self.approval_repo,
                admin_token=self.ADMIN_KEY,
                read_api_key=self.READ_KEY,
                control_api_key=self.CONTROL_KEY,
            )
        )

    def _preview(self, body: dict | None = None, key: str | None = None):
        return self.client.post(
            "/api/v1/events/hays-fy2026-results/strategy-draft/preview",
            headers={"X-MarketAI-Key": key if key is not None else self.READ_KEY},
            json=body or _valid_draft_body(),
        )

    def _approve(self, body: dict, key: str | None = None):
        headers = {}
        if key is not None:
            headers["X-MarketAI-Control-Key"] = key
        return self.client.post(
            "/api/v1/events/hays-fy2026-results/strategy-draft/approve",
            headers=headers,
            json=body,
        )

    def _approval_body_from_preview(self, preview_body: dict, **overrides) -> dict:
        preview = self._preview(preview_body).json()
        body = {
            "draft": preview_body,
            "draft_fingerprint": preview["draft_fingerprint"],
            "base_expectation_version": preview["base_expectation_version"],
            "approved_by": "marko",
            "approved_via": "mobile-app",
        }
        body.update(overrides)
        return body

    # -- preview: read-tier auth, never writes ----------------------------

    def test_preview_requires_read_key(self) -> None:
        denied = self._preview(key="")
        self.assertEqual(denied.status_code, 401)

        allowed = self._preview()
        self.assertEqual(allowed.status_code, 200)

    def test_preview_does_not_change_the_current_expectation_version(self) -> None:
        response = self._preview()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)

    def test_preview_never_touches_the_paper_repository(self) -> None:
        # No paper-trade run may be created/consulted as a side effect of
        # previewing a draft - the paper repository must simply never be
        # called by this endpoint.
        response = self._preview()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.paper_repo.calls, 0)

    def test_preview_returns_fingerprint_current_and_warnings(self) -> None:
        response = self._preview()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["base_expectation_version"], 1)
        self.assertIn("draft_fingerprint", body)
        self.assertEqual(body["current"]["version"], 1)
        self.assertIn("changed_fields", body)
        self.assertIn("triggers", body["changed_fields"])
        self.assertIsInstance(body["warnings"], list)

    def test_preview_missing_required_field_is_rejected(self) -> None:
        body = _valid_draft_body()
        del body["summary"]

        response = self._preview(body)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)

    def test_preview_rejects_whitespace_only_summary_and_change_note(self) -> None:
        for field, value in (("summary", "   "), ("change_note", "\t\n  ")):
            with self.subTest(field=field):
                response = self._preview(_valid_draft_body(**{field: value}))

                self.assertEqual(response.status_code, 422)

    def test_approve_rejects_whitespace_only_summary_and_change_note(self) -> None:
        # Even if a caller somehow builds an approval body bypassing
        # preview's own validation, approve() re-validates the same
        # StrategyDraftPayload and must reject it too - a whitespace-only
        # change_note/summary can never reach the persisted expectation
        # version or the audit trail.
        for field, value in (("summary", "   "), ("change_note", "\t\n  ")):
            with self.subTest(field=field):
                approval_body = {
                    "draft": _valid_draft_body(**{field: value}),
                    "draft_fingerprint": "0" * 64,
                    "base_expectation_version": 1,
                    "approved_by": "marko",
                }

                response = self._approve(approval_body, key=self.CONTROL_KEY)

                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)
                self.assertEqual(self.approval_repo.audit_records, [])

    def test_approve_rejects_whitespace_only_approved_by(self) -> None:
        for value in ("   ", "\t\n  "):
            with self.subTest(value=repr(value)):
                approval_body = self._approval_body_from_preview(
                    _valid_draft_body(), approved_by=value
                )

                response = self._approve(approval_body, key=self.CONTROL_KEY)

                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)
                # A whitespace-only identity must never reach the audit
                # trail, whitespace-padded or otherwise.
                self.assertEqual(self.approval_repo.audit_records, [])

    def test_approve_strips_approved_by_before_recording_it(self) -> None:
        approval_body = self._approval_body_from_preview(
            _valid_draft_body(), approved_by="  marko  "
        )

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["approved_by"], "marko")
        self.assertEqual(self.approval_repo.audit_records[0]["approved_by"], "marko")

    def test_preview_unknown_event_is_404(self) -> None:
        response = self.client.post(
            "/api/v1/events/does-not-exist/strategy-draft/preview",
            headers={"X-MarketAI-Key": self.READ_KEY},
            json=_valid_draft_body(),
        )

        self.assertEqual(response.status_code, 404)

    def test_consensus_null_round_trips_through_preview_as_real_null(self) -> None:
        draft = _valid_draft_body(
            consensus={
                "fy27_operating_profit_pre_exceptional_gbp_m": None,
                "other_metric_gbp_m": 55.6,
            }
        )

        response = self._preview(draft)

        self.assertEqual(response.status_code, 200)
        consensus = response.json()["draft"]["consensus"]
        self.assertIsNone(consensus["fy27_operating_profit_pre_exceptional_gbp_m"])
        self.assertNotEqual(consensus["fy27_operating_profit_pre_exceptional_gbp_m"], "null")
        self.assertEqual(consensus["other_metric_gbp_m"], 55.6)

    # -- approve: strong write-auth ----------------------------------------

    def test_approve_without_control_key_is_401(self) -> None:
        approval_body = self._approval_body_from_preview(_valid_draft_body())

        response = self._approve(approval_body)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)
        self.assertEqual(self.approval_repo.audit_records, [])

    def test_read_key_cannot_approve(self) -> None:
        approval_body = self._approval_body_from_preview(_valid_draft_body())

        response = self._approve(approval_body, key=self.READ_KEY)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)

    def test_admin_token_cannot_approve(self) -> None:
        # The control credential is independent of the admin token - the
        # admin token must not double as control-auth any more than it
        # doubles as the read key.
        approval_body = self._approval_body_from_preview(_valid_draft_body())

        response = self._approve(approval_body, key=self.ADMIN_KEY)

        self.assertEqual(response.status_code, 401)

    def test_unset_control_key_fails_closed(self) -> None:
        client = TestClient(
            create_app(
                self.repo,
                paper_repository=self.paper_repo,
                approval_repository=self.approval_repo,
                admin_token=self.ADMIN_KEY,
                read_api_key=self.READ_KEY,
                control_api_key=None,
            )
        )
        approval_body = self._approval_body_from_preview(_valid_draft_body())

        response = client.post(
            "/api/v1/events/hays-fy2026-results/strategy-draft/approve",
            headers={"X-MarketAI-Control-Key": self.CONTROL_KEY},
            json=approval_body,
        )

        self.assertEqual(response.status_code, 503)

    def test_authorized_approve_creates_a_new_expectation_version(self) -> None:
        draft = _valid_draft_body()
        approval_body = self._approval_body_from_preview(draft)

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["version"], 2)
        self.assertEqual(body["triggers"]["bull_fy27_operating_profit_gbp_m"], 62.0)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 2)

    def test_consensus_null_round_trips_through_approve_as_real_null(self) -> None:
        draft = _valid_draft_body(
            consensus={
                "fy27_operating_profit_pre_exceptional_gbp_m": None,
                "other_metric_gbp_m": 55.6,
            },
            important_kpis=[],
        )
        approval_body = self._approval_body_from_preview(draft)

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 201)
        consensus = response.json()["consensus"]
        self.assertIsNone(consensus["fy27_operating_profit_pre_exceptional_gbp_m"])
        self.assertNotEqual(consensus["fy27_operating_profit_pre_exceptional_gbp_m"], "null")

    def test_colon_containing_keys_round_trip_through_preview_and_approve(self) -> None:
        draft = _valid_draft_body(
            consensus={"Revenue: FY27": 123.4},
            triggers={"bull_Revenue: FY27": 130.0},
            important_kpis=[],
        )

        preview = self._preview(draft)
        self.assertEqual(preview.status_code, 200)
        preview_body = preview.json()
        self.assertEqual(preview_body["draft"]["consensus"]["Revenue: FY27"], 123.4)
        self.assertEqual(preview_body["draft"]["triggers"]["bull_Revenue: FY27"], 130.0)

        approval_body = self._approval_body_from_preview(draft)
        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["consensus"]["Revenue: FY27"], 123.4)
        self.assertEqual(body["triggers"]["bull_Revenue: FY27"], 130.0)
        persisted = self.repo.get("hays-fy2026-results")
        self.assertEqual(persisted.consensus["Revenue: FY27"], 123.4)
        self.assertEqual(persisted.triggers["bull_Revenue: FY27"], 130.0)

    def test_approve_records_an_audit_trail_entry(self) -> None:
        draft = _valid_draft_body()
        approval_body = self._approval_body_from_preview(draft, approved_by="marko@example.com")

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.approval_repo.audit_records), 1)
        record = self.approval_repo.audit_records[0]
        self.assertEqual(record["event_id"], "hays-fy2026-results")
        self.assertEqual(record["expectation_version"], 2)
        self.assertEqual(record["base_expectation_version"], 1)
        self.assertEqual(record["approved_by"], "marko@example.com")
        self.assertEqual(record["draft_fingerprint"], approval_body["draft_fingerprint"])

    def test_approve_with_stale_expectation_version_is_a_conflict(self) -> None:
        draft = _valid_draft_body()
        approval_body = self._approval_body_from_preview(draft)

        # Someone else's approval lands first, advancing the version.
        other_draft = _valid_draft_body(change_note="A different, earlier approval")
        other_body = self._approval_body_from_preview(other_draft)
        first = self._approve(other_body, key=self.CONTROL_KEY)
        self.assertEqual(first.status_code, 201)

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 2)

    def test_approve_with_draft_changed_since_preview_is_a_conflict(self) -> None:
        draft = _valid_draft_body()
        approval_body = self._approval_body_from_preview(draft)
        # Tamper with the draft after the fingerprint was computed.
        approval_body["draft"]["summary"] = "A different summary the preview never saw"

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)

    def test_approve_with_mismatched_fingerprint_string_is_a_conflict(self) -> None:
        draft = _valid_draft_body()
        approval_body = self._approval_body_from_preview(draft)
        approval_body["draft_fingerprint"] = "0" * 64

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 409)

    def test_approve_rejects_malformed_fingerprints_without_ever_500ing(self) -> None:
        # secrets.compare_digest() raises TypeError on a non-ASCII string
        # argument - a malformed draft_fingerprint must never reach that
        # call at all. Field(pattern=...) rejects it (422) at the request
        # boundary first, for every shape of "malformed" including the
        # exact one that used to crash the comparison.
        draft = _valid_draft_body()
        approval_body = self._approval_body_from_preview(draft)
        malformed_fingerprints = {
            "too_short": "a" * 63,
            "too_long": "a" * 65,
            "non_hex_chars": "g" * 64,
            "non_ascii": "ñ" * 64,
            "empty": "",
            "whitespace_only": " " * 64,
            "hex_with_uppercase_and_symbols": "A1b2!" + "0" * 59,
        }
        for label, fingerprint in malformed_fingerprints.items():
            with self.subTest(label=label):
                body = dict(approval_body)
                body["draft_fingerprint"] = fingerprint

                response = self._approve(body, key=self.CONTROL_KEY)

                self.assertEqual(response.status_code, 422)
                self.assertLess(response.status_code, 500)
        # Nothing malformed ever reached persistence or the audit trail.
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)
        self.assertEqual(self.approval_repo.audit_records, [])

    def test_approve_normalizes_fingerprint_case_before_comparing(self) -> None:
        # Uppercase (or mixed-case) hex is valid SHA-256 hex-digest syntax
        # too, even though draft_fingerprint() itself only ever emits
        # lowercase. A semantically identical digest must never be
        # rejected as "changed since preview" just because of letter case -
        # this is not a legitimate conflict, and not a validation error.
        draft = _valid_draft_body()
        preview = self._preview(draft).json()
        approval_body = {
            "draft": draft,
            "draft_fingerprint": preview["draft_fingerprint"].upper(),
            "base_expectation_version": preview["base_expectation_version"],
            "approved_by": "marko",
        }

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 2)

    def test_approve_records_the_canonical_lowercase_fingerprint_in_the_audit_trail(
        self,
    ) -> None:
        draft = _valid_draft_body()
        preview = self._preview(draft).json()
        lowercase_fingerprint = preview["draft_fingerprint"]
        approval_body = {
            "draft": draft,
            "draft_fingerprint": lowercase_fingerprint.upper(),
            "base_expectation_version": preview["base_expectation_version"],
            "approved_by": "marko",
        }

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 201)
        # The response and the audit record both store the canonical
        # (lowercase) form draft_fingerprint() itself produces, not
        # whatever case the caller happened to submit.
        self.assertEqual(response.json()["draft_fingerprint"], lowercase_fingerprint)
        self.assertEqual(
            self.approval_repo.audit_records[0]["draft_fingerprint"], lowercase_fingerprint
        )

    def test_mixed_case_fingerprint_with_a_real_mismatch_still_conflicts(self) -> None:
        # Case-normalization must not weaken the actual integrity check -
        # a mixed-case fingerprint that genuinely doesn't match the
        # recomputed one is still a real conflict.
        draft = _valid_draft_body()
        approval_body = self._approval_body_from_preview(draft)
        approval_body["draft_fingerprint"] = ("aB" * 32)  # well-formed hex, but wrong digest

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 409)

    def test_repeated_identical_approve_does_not_create_duplicate_versions(self) -> None:
        draft = _valid_draft_body()
        approval_body = self._approval_body_from_preview(draft)

        first = self._approve(approval_body, key=self.CONTROL_KEY)
        second = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["version"], 2)
        # The retried/duplicate call must fail (its base_expectation_version
        # is now stale) rather than silently creating version 3.
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 2)
        self.assertEqual(len(self.approval_repo.audit_records), 1)

    def test_approve_unknown_event_is_404(self) -> None:
        approval_body = {
            "draft": _valid_draft_body(),
            "draft_fingerprint": "0" * 64,
            "base_expectation_version": 1,
            "approved_by": "marko",
        }
        response = self.client.post(
            "/api/v1/events/does-not-exist/strategy-draft/approve",
            headers={"X-MarketAI-Control-Key": self.CONTROL_KEY},
            json=approval_body,
        )

        self.assertEqual(response.status_code, 404)

    # -- draft never leaks into what the worker/paper-trading path sees ----

    def test_preview_never_calls_repository_save(self) -> None:
        original_save = self.repo.save
        calls: list[bool] = []

        def spy_save(*args, **kwargs):
            calls.append(True)
            return original_save(*args, **kwargs)

        self.repo.save = spy_save  # type: ignore[assignment]

        self._preview()

        self.assertEqual(calls, [])

    def test_only_the_approved_version_is_visible_through_list_upcoming(self) -> None:
        # list_upcoming() / get() is exactly what the release worker reads
        # to decide trades - a preview must never appear there, and an
        # approved draft must appear only as the new current version.
        before = self.repo.list_upcoming()
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0].version, 1)

        self._preview()
        after_preview = self.repo.list_upcoming()
        self.assertEqual(after_preview[0].version, 1)

        approval_body = self._approval_body_from_preview(_valid_draft_body())
        self._approve(approval_body, key=self.CONTROL_KEY)

        after_approve = self.repo.list_upcoming()
        self.assertEqual(after_approve[0].version, 2)

    def test_approve_never_calls_repository_save_directly(self) -> None:
        # CAS must be enforced by the atomic approval repository, never by
        # EventExpectationRepository.save()'s own max(version)+1 retry loop -
        # that loop has no way to check "the version I previewed against is
        # still current."
        original_save = self.repo.save
        calls: list[bool] = []

        def spy_save(*args, **kwargs):
            calls.append(True)
            return original_save(*args, **kwargs)

        self.repo.save = spy_save  # type: ignore[assignment]

        approval_body = self._approval_body_from_preview(_valid_draft_body())
        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(calls, [])

    # -- identity: {event_id} in the URL is authoritative -------------------

    def test_identity_mismatch_is_only_a_warning_in_preview(self) -> None:
        drifted = _valid_draft_body(instrument="DIFFERENT.L")

        response = self._preview(drifted)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("instrument" in w for w in response.json()["warnings"]))

    def test_identity_mismatch_hard_fails_approval(self) -> None:
        for field, value in (
            ("instrument", "DIFFERENT.L"),
            ("event_name", "A completely different event"),
            ("scheduled_date", "2099-01-01"),
        ):
            with self.subTest(field=field):
                drifted = _valid_draft_body(**{field: value})
                approval_body = self._approval_body_from_preview(drifted)

                response = self._approve(approval_body, key=self.CONTROL_KEY)

                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)
                self.assertEqual(self.approval_repo.audit_records, [])

    # -- atomicity: concurrency and partial-failure regressions -------------

    def test_two_concurrent_approvals_from_the_same_base_version_only_one_succeeds(
        self,
    ) -> None:
        approval_body = self._approval_body_from_preview(_valid_draft_body())

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._approve, approval_body, self.CONTROL_KEY)
                for _ in range(2)
            ]
            statuses = sorted(future.result().status_code for future in futures)

        self.assertEqual(statuses, [201, 409])
        # Exactly one new expectation version ...
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 2)
        # ... and exactly one audit entry - never two, and never zero.
        self.assertEqual(len(self.approval_repo.audit_records), 1)

    def test_audit_insert_failure_leaves_the_expectation_unchanged(self) -> None:
        # Simulates the audit insert failing inside the same transaction as
        # the expectation-version insert (see the Postgres function this
        # in-memory repository mirrors): nothing may be left half-written.
        self.approval_repo.fail_audit_insert = True
        approval_body = self._approval_body_from_preview(_valid_draft_body())

        response = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 1)
        self.assertEqual(self.approval_repo.audit_records, [])

        # And a subsequent, non-faulty approval against the same still-valid
        # base version succeeds normally afterwards.
        self.approval_repo.fail_audit_insert = False
        retry = self._approve(approval_body, key=self.CONTROL_KEY)

        self.assertEqual(retry.status_code, 201)
        self.assertEqual(self.repo.get("hays-fy2026-results").version, 2)
        self.assertEqual(len(self.approval_repo.audit_records), 1)

    def test_concurrent_admin_write_and_approval_never_5xx(self) -> None:
        # The admin direct-write endpoint and the strategy-draft approval
        # endpoint both allocate the next event_expectation_versions row.
        # In production they now take the same pg_advisory_xact_lock (see
        # supabase/migrations/20260821140000_shared_expectation_version_lock.sql);
        # the in-memory doubles mirror that by sharing one lock object
        # (InMemoryStrategyDraftApprovalRepository.expectations.lock). A
        # race between them must never surface as an unhandled 500 - the
        # admin write always succeeds (it has no base-version expectation
        # of its own), and approve either succeeds or gets a clean 409 if
        # the admin write happened to land first and moved the version out
        # from under it.
        approval_body = self._approval_body_from_preview(_valid_draft_body())

        def do_admin_write():
            return self.client.post(
                "/api/v1/events/hays-fy2026-results/expectation-versions",
                headers={"X-Admin-Token": self.ADMIN_KEY},
                json={
                    "change_note": "concurrent admin edit",
                    "triggers": {"bull_fy27_operating_profit_gbp_m": 99.0},
                },
            )

        def do_approve():
            return self._approve(approval_body, key=self.CONTROL_KEY)

        with ThreadPoolExecutor(max_workers=2) as executor:
            admin_future = executor.submit(do_admin_write)
            approve_future = executor.submit(do_approve)
            admin_response = admin_future.result()
            approve_response = approve_future.result()

        self.assertLess(admin_response.status_code, 500)
        self.assertLess(approve_response.status_code, 500)
        self.assertEqual(admin_response.status_code, 201)
        self.assertIn(approve_response.status_code, (201, 409))

        # No matter which order they actually ran in, exactly one final
        # version results - never a crash, never two colliding version 2s.
        final = self.repo.get("hays-fy2026-results")
        self.assertIsNotNone(final)
        self.assertGreaterEqual(final.version, 2)


if __name__ == "__main__":
    unittest.main()
