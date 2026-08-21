import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from trading_system.event_repository import InMemoryEventExpectationRepository
from trading_system.models import EventExpectation
from trading_system.strategy_draft_repository import (
    ExpectationVersionConflict,
    InMemoryStrategyDraftApprovalRepository,
    StrategyDraftEventNotFound,
    SupabaseStrategyDraftApprovalRepository,
)


def _expectation(**overrides: Any) -> EventExpectation:
    defaults: dict[str, Any] = dict(
        event_id="hays-fy2026-results",
        instrument="HAS.L",
        event_name="Hays plc FY2026 results",
        scheduled_date=date(2026, 8, 20),
        consensus={"fy27_operating_profit_pre_exceptional_gbp_m": 55.6},
        version=1,
    )
    defaults.update(overrides)
    return EventExpectation(**defaults)


def _approve_kwargs(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = dict(
        event_id="hays-fy2026-results",
        expected_base_version=1,
        source_name="Hays plc analyst consensus",
        source_url="https://example.com/consensus",
        source_as_of=date(2026, 7, 15),
        consensus={"fy27_operating_profit_pre_exceptional_gbp_m": 55.6},
        important_kpis=["fy27_operating_profit_pre_exceptional_gbp_m"],
        bull_case=["Fees grow above consensus"],
        base_case=["In-line results"],
        bear_case=["Margin compression"],
        triggers={"bull_fy27_operating_profit_gbp_m": 62.0},
        invalidation_conditions=["Guidance withdrawn"],
        change_note="raise bull threshold",
        draft_fingerprint="a" * 64,
        approved_by="marko",
        approved_via="mobile-app",
    )
    defaults.update(overrides)
    return defaults


class InMemoryStrategyDraftApprovalRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.expectations = InMemoryEventExpectationRepository(
            events={"hays-fy2026-results": _expectation()}
        )
        self.repo = InMemoryStrategyDraftApprovalRepository(expectations=self.expectations)

    def test_approve_advances_the_version_and_records_audit(self) -> None:
        result = self.repo.approve(**_approve_kwargs())

        self.assertEqual(result.version, 2)
        self.assertEqual(self.expectations.events["hays-fy2026-results"].version, 2)
        self.assertEqual(
            self.expectations.events["hays-fy2026-results"].triggers[
                "bull_fy27_operating_profit_gbp_m"
            ],
            62.0,
        )
        self.assertEqual(len(self.repo.audit_records), 1)
        entry = self.repo.audit_records[0]
        self.assertEqual(entry["expectation_version"], 2)
        self.assertEqual(entry["base_expectation_version"], 1)
        self.assertEqual(entry["approved_by"], "marko")

    def test_stale_base_version_raises_conflict_without_any_side_effect(self) -> None:
        with self.assertRaises(ExpectationVersionConflict):
            self.repo.approve(**_approve_kwargs(expected_base_version=99))

        self.assertEqual(self.expectations.events["hays-fy2026-results"].version, 1)
        self.assertEqual(self.repo.audit_records, [])

    def test_unknown_event_raises_not_found(self) -> None:
        with self.assertRaises(StrategyDraftEventNotFound):
            self.repo.approve(**_approve_kwargs(event_id="does-not-exist"))

    def test_audit_insert_failure_leaves_the_expectation_untouched(self) -> None:
        self.repo.fail_audit_insert = True

        with self.assertRaises(RuntimeError):
            self.repo.approve(**_approve_kwargs())

        self.assertEqual(self.expectations.events["hays-fy2026-results"].version, 1)
        self.assertEqual(self.repo.audit_records, [])

        self.repo.fail_audit_insert = False
        result = self.repo.approve(**_approve_kwargs())
        self.assertEqual(result.version, 2)

    def test_two_concurrent_approvals_from_the_same_base_version_only_one_wins(self) -> None:
        # A lock serializes the critical section (mirrors
        # pg_advisory_xact_lock in the Postgres function): whichever caller
        # gets there second must see the already-advanced version and fail,
        # never race it into producing two new versions.
        start_barrier = threading.Barrier(2)
        results: list[Any] = []
        errors: list[Exception] = []

        def call() -> None:
            start_barrier.wait()
            try:
                results.append(self.repo.approve(**_approve_kwargs()))
            except ExpectationVersionConflict as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda _: call(), range(2)))

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.expectations.events["hays-fy2026-results"].version, 2)
        self.assertEqual(len(self.repo.audit_records), 1)


class _RpcCall:
    def __init__(self, name: str, params: dict[str, Any], response: Any) -> None:
        self.name = name
        self.params = params
        self._response = response

    def execute(self) -> Any:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _RecordingClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[_RpcCall] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _RpcCall:
        call = _RpcCall(name, params, self.response)
        self.calls.append(call)
        return call


class _Response:
    def __init__(self, data: Any) -> None:
        self.data = data


class SupabaseStrategyDraftApprovalRepositoryTests(unittest.TestCase):
    def test_approve_calls_the_rpc_with_mapped_params_and_parses_the_result(self) -> None:
        client = _RecordingClient(
            _Response([{"new_version": 2, "created_at": "2026-08-21T09:00:00+00:00"}])
        )
        repo = SupabaseStrategyDraftApprovalRepository(client)

        result = repo.approve(**_approve_kwargs())

        self.assertEqual(result.version, 2)
        self.assertEqual(result.updated_at.year, 2026)
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call.name, "approve_strategy_draft")
        self.assertEqual(call.params["input_event_id"], "hays-fy2026-results")
        self.assertEqual(call.params["input_expected_base_version"], 1)
        self.assertEqual(call.params["input_source_as_of"], "2026-07-15")
        self.assertEqual(
            call.params["input_important_kpis"],
            ["fy27_operating_profit_pre_exceptional_gbp_m"],
        )
        self.assertEqual(call.params["input_draft_fingerprint"], "a" * 64)

    def test_version_conflict_error_from_the_rpc_is_translated(self) -> None:
        error = RuntimeError("expectation_version_conflict: expected 1 but current is 2")
        client = _RecordingClient(error)
        repo = SupabaseStrategyDraftApprovalRepository(client)

        with self.assertRaises(ExpectationVersionConflict):
            repo.approve(**_approve_kwargs())

    def test_event_not_found_error_from_the_rpc_is_translated(self) -> None:
        error = RuntimeError("event_not_found: does-not-exist")
        client = _RecordingClient(error)
        repo = SupabaseStrategyDraftApprovalRepository(client)

        with self.assertRaises(StrategyDraftEventNotFound):
            repo.approve(**_approve_kwargs())

    def test_unrelated_error_from_the_rpc_propagates_unchanged(self) -> None:
        error = RuntimeError("connection reset")
        client = _RecordingClient(error)
        repo = SupabaseStrategyDraftApprovalRepository(client)

        with self.assertRaises(RuntimeError):
            repo.approve(**_approve_kwargs())


if __name__ == "__main__":
    unittest.main()
