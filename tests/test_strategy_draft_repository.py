import unittest
from typing import Any

from trading_system.strategy_draft_repository import (
    InMemoryStrategyApprovalAuditRepository,
    SupabaseStrategyApprovalAuditRepository,
)


class _InsertQuery:
    def __init__(self, payload: dict[str, Any], calls: list[dict[str, Any]]) -> None:
        self.payload = payload
        self.calls = calls

    def execute(self) -> None:
        self.calls.append(self.payload)


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.table_name: str | None = None

    def table(self, name: str) -> "_RecordingClient":
        self.table_name = name
        return self

    def insert(self, payload: dict[str, Any]) -> _InsertQuery:
        return _InsertQuery(payload, self.calls)


class InMemoryStrategyApprovalAuditRepositoryTests(unittest.TestCase):
    def test_record_appends_a_full_entry(self) -> None:
        repo = InMemoryStrategyApprovalAuditRepository()

        repo.record(
            event_id="hays-fy2026-results",
            expectation_version=2,
            base_expectation_version=1,
            draft_fingerprint="a" * 64,
            approved_by="marko",
            approved_via="mobile-app",
            change_note="raise bull threshold",
        )

        self.assertEqual(len(repo.records), 1)
        entry = repo.records[0]
        self.assertEqual(entry["event_id"], "hays-fy2026-results")
        self.assertEqual(entry["expectation_version"], 2)
        self.assertEqual(entry["base_expectation_version"], 1)
        self.assertEqual(entry["draft_fingerprint"], "a" * 64)
        self.assertEqual(entry["approved_by"], "marko")
        self.assertEqual(entry["approved_via"], "mobile-app")
        self.assertIn("created_at", entry)


class SupabaseStrategyApprovalAuditRepositoryTests(unittest.TestCase):
    def test_record_inserts_into_event_strategy_approvals(self) -> None:
        client = _RecordingClient()
        repo = SupabaseStrategyApprovalAuditRepository(client)

        repo.record(
            event_id="hays-fy2026-results",
            expectation_version=2,
            base_expectation_version=1,
            draft_fingerprint="b" * 64,
            approved_by="chatgpt-assistant",
            approved_via="control-api",
            change_note="raise bull threshold",
        )

        self.assertEqual(client.table_name, "event_strategy_approvals")
        self.assertEqual(len(client.calls), 1)
        payload = client.calls[0]
        self.assertEqual(payload["event_id"], "hays-fy2026-results")
        self.assertEqual(payload["expectation_version"], 2)
        self.assertEqual(payload["base_expectation_version"], 1)
        self.assertEqual(payload["draft_fingerprint"], "b" * 64)
        self.assertEqual(payload["approved_by"], "chatgpt-assistant")
        self.assertEqual(payload["approved_via"], "control-api")
        self.assertEqual(payload["change_note"], "raise bull threshold")


if __name__ == "__main__":
    unittest.main()
