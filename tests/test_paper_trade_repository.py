import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from trading_system.models import ComponentAssessment, Direction
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.post_release_paper import PostReleasePaperResult


TERMINAL = {"expired_no_trade", "paper_executed"}


class _TableQuery:
    def __init__(self, client: "_AtomicClient") -> None:
        self.client = client

    def select(self, *_args: Any) -> "_TableQuery":
        return self

    def eq(self, *_args: Any) -> "_TableQuery":
        return self

    def order(self, *_args: Any, **_kwargs: Any) -> "_TableQuery":
        return self

    def limit(self, *_args: Any) -> "_TableQuery":
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[self.client.row] if self.client.row else [])


class _RpcQuery:
    def __init__(self, client: "_AtomicClient", name: str, params: dict[str, Any]) -> None:
        self.client = client
        self.name = name
        self.params = params

    def execute(self) -> SimpleNamespace:
        current = self.client.row
        if self.name == "save_event_paper_trade_result":
            incoming = self.params["input_payload"].copy()
            self.client.save_calls.append(incoming)
            if current and current["status"] in TERMINAL:
                return SimpleNamespace(data=[])
            self.client.row = incoming
            return SimpleNamespace(data=[incoming])

        assert self.name == "expire_event_paper_trade_run"
        self.client.expire_calls.append(self.params.copy())
        if current and current["status"] != "waiting_confirmation":
            return SimpleNamespace(data=[])
        deadline = self.params["input_confirmation_deadline_at"]
        self.client.row = {
            **(current or {}),
            "event_id": self.params["input_event_id"],
            "analysis_id": self.params["input_analysis_id"],
            "status": "expired_no_trade",
            "confirmation_deadline_at": (current or {}).get("confirmation_deadline_at")
            or deadline,
            "expired_at": self.params["input_expired_at"],
        }
        return SimpleNamespace(data=[self.client.row])


class _AtomicClient:
    """In-memory model of the migration's atomic terminal-state predicates."""

    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.save_calls: list[dict[str, Any]] = []
        self.expire_calls: list[dict[str, Any]] = []

    def table(self, _table: str) -> _TableQuery:
        return _TableQuery(self)

    def rpc(self, name: str, params: dict[str, Any]) -> _RpcQuery:
        return _RpcQuery(self, name, params)


class PaperTradeRepositoryTests(unittest.TestCase):
    deadline = datetime(2026, 8, 20, 15, 45, tzinfo=UTC)
    expired_at = datetime(2026, 8, 20, 15, 46, tzinfo=UTC)

    def save(self, client: _AtomicClient, status: str) -> dict[str, Any]:
        return SupabasePaperTradeRepository(client).save_result(
            event_id="hays-fy2026-results",
            expectation_version=1,
            source_document_id="00000000-0000-0000-0000-000000000001",
            analysis_id="00000000-0000-0000-0000-000000000002",
            result=PostReleasePaperResult(
                status,
                "result",
                confirmation_deadline_at=self.deadline,
            ),
        )

    def expire(self, client: _AtomicClient) -> dict[str, Any] | None:
        return SupabasePaperTradeRepository(client).expire_waiting(
            event_id="hays-fy2026-results",
            expectation_version=1,
            source_document_id="00000000-0000-0000-0000-000000000001",
            analysis_id="00000000-0000-0000-0000-000000000002",
            confirmation_deadline_at=self.deadline,
            expired_at=self.expired_at,
        )

    def test_waiting_result_persists_completed_components_and_deadline(self) -> None:
        client = _AtomicClient()
        result = PostReleasePaperResult(
            "waiting_confirmation",
            "technical confirmation is not aligned",
            completed_components=(
                ComponentAssessment("fundamental", Direction.LONG, 24, 35),
                ComponentAssessment("catalyst", Direction.LONG, 20, 25),
            ),
            confirmation_deadline_at=self.deadline,
        )
        SupabasePaperTradeRepository(client).save_result(
            event_id="hays-fy2026-results",
            expectation_version=1,
            source_document_id="00000000-0000-0000-0000-000000000001",
            analysis_id="00000000-0000-0000-0000-000000000002",
            result=result,
        )
        payload = client.save_calls[0]
        self.assertEqual(payload["confirmation_deadline_at"], self.deadline.isoformat())
        self.assertEqual(payload["completed_components"]["fundamental"]["score"], 24)
        self.assertEqual(payload["completed_components"]["catalyst"]["score"], 20)

    def test_expired_cannot_return_to_waiting(self) -> None:
        client = _AtomicClient({"status": "expired_no_trade"})
        winner = self.save(client, "waiting_confirmation")
        self.assertEqual(client.row["status"], "expired_no_trade")
        self.assertEqual(winner["status"], "expired_no_trade")

    def test_expired_cannot_become_paper_executed_by_stale_writer(self) -> None:
        client = _AtomicClient({"status": "expired_no_trade"})
        winner = self.save(client, "paper_executed")
        self.assertEqual(client.row["status"], "expired_no_trade")
        self.assertEqual(winner["status"], "expired_no_trade")

    def test_paper_executed_cannot_become_expired(self) -> None:
        client = _AtomicClient({"status": "paper_executed"})
        winner = self.expire(client)
        self.assertEqual(client.row["status"], "paper_executed")
        assert winner is not None
        self.assertEqual(winner["status"], "paper_executed")

    def test_concurrent_stale_save_loses_to_terminal_state(self) -> None:
        client = _AtomicClient({"status": "waiting_confirmation"})
        self.expire(client)
        self.save(client, "paper_executed")
        self.assertEqual(client.row["status"], "expired_no_trade")

    def test_expiry_backfills_missing_deadline_on_waiting_row(self) -> None:
        client = _AtomicClient(
            {"status": "waiting_confirmation", "confirmation_deadline_at": None}
        )
        expired = self.expire(client)
        assert expired is not None
        self.assertEqual(expired["confirmation_deadline_at"], self.deadline.isoformat())


if __name__ == "__main__":
    unittest.main()
