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


class _RejectedRpcQuery:
    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[])


class _AnalysisQuery:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def select(self, *_args: Any) -> "_AnalysisQuery":
        return self

    def eq(self, column: str, value: str) -> "_AnalysisQuery":
        self.rows = [row for row in self.rows if row.get(column) == value]
        return self

    def order(self, column: str, *, desc: bool = False) -> "_AnalysisQuery":
        self.rows.sort(key=lambda row: row.get(column, ""), reverse=desc)
        return self

    def limit(self, count: int) -> "_AnalysisQuery":
        self.rows = self.rows[:count]
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self.rows)


class _RejectedSaveClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def rpc(self, _name: str, _params: dict[str, Any]) -> _RejectedRpcQuery:
        return _RejectedRpcQuery()

    def table(self, _table: str) -> _AnalysisQuery:
        return _AnalysisQuery(self.rows.copy())


class _EventRpcQuery:
    def __init__(self, client: "_EventAtomicClient", name: str, params: dict[str, Any]) -> None:
        self.client = client
        self.name = name
        self.params = params

    def execute(self) -> SimpleNamespace:
        if self.name == "claim_event_paper_run":
            event_id = self.params["input_event_id"]
            analysis_id = self.params["input_analysis_id"]
            owner = self.client.claims.setdefault(event_id, analysis_id)
            return SimpleNamespace(
                data=[{"event_id": event_id, "analysis_id": owner}]
            )
        if self.name == "save_event_paper_trade_result":
            incoming = self.params["input_payload"].copy()
            event_id = incoming["event_id"]
            analysis_id = incoming["analysis_id"]
        else:
            event_id = self.params["input_event_id"]
            analysis_id = self.params["input_analysis_id"]
            incoming = {
                "event_id": event_id,
                "analysis_id": analysis_id,
                "status": "expired_no_trade",
            }

        owner = next(
            (
                row
                for row in self.client.rows
                if row["event_id"] == event_id and row["status"] in TERMINAL
            ),
            None,
        )
        if owner is not None:
            return SimpleNamespace(data=[owner.copy()])

        existing = next(
            (row for row in self.client.rows if row["analysis_id"] == analysis_id),
            None,
        )
        if existing is None:
            self.client.rows.append(incoming)
            saved = incoming
        else:
            existing.update(incoming)
            saved = existing
        return SimpleNamespace(data=[saved.copy()])


class _EventAtomicClient:
    """Models the migration's event advisory lock and terminal-owner check."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.claims: dict[str, str] = {}

    def rpc(self, name: str, params: dict[str, Any]) -> _EventRpcQuery:
        return _EventRpcQuery(self, name, params)

    def table(self, _table: str) -> _AnalysisQuery:
        return _AnalysisQuery(self.rows.copy())


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

    def test_rejected_save_returns_terminal_row_for_same_analysis(self) -> None:
        analysis_id = "00000000-0000-0000-0000-000000000002"
        client = _RejectedSaveClient(
            [{"analysis_id": analysis_id, "status": "expired_no_trade"}]
        )
        winner = self.save(client, "paper_executed")
        self.assertEqual(winner["analysis_id"], analysis_id)
        self.assertEqual(winner["status"], "expired_no_trade")

    def test_rejected_save_without_authoritative_row_fails_closed(self) -> None:
        with self.assertRaises(RuntimeError):
            self.save(_RejectedSaveClient([]), "paper_executed")

    def test_rejected_save_for_old_analysis_never_reads_newer_analysis(self) -> None:
        analysis_a = "00000000-0000-0000-0000-000000000002"
        analysis_b = "00000000-0000-0000-0000-000000000003"
        client = _RejectedSaveClient(
            [
                {
                    "analysis_id": analysis_a,
                    "status": "expired_no_trade",
                    "updated_at": "2026-08-20T15:45:00+00:00",
                },
                {
                    "analysis_id": analysis_b,
                    "status": "paper_executed",
                    "updated_at": "2026-08-20T15:50:00+00:00",
                },
            ]
        )
        winner = self.save(client, "paper_executed")
        self.assertEqual(winner["analysis_id"], analysis_a)
        self.assertEqual(winner["status"], "expired_no_trade")

    def test_expired_analysis_wins_even_when_newer_analysis_is_waiting(self) -> None:
        analysis_a = "00000000-0000-0000-0000-000000000002"
        analysis_b = "00000000-0000-0000-0000-000000000003"
        client = _RejectedSaveClient(
            [
                {"analysis_id": analysis_a, "status": "expired_no_trade"},
                {"analysis_id": analysis_b, "status": "waiting_confirmation"},
            ]
        )
        winner = self.save(client, "paper_executed")
        self.assertEqual(winner["analysis_id"], analysis_a)
        self.assertEqual(winner["status"], "expired_no_trade")

    def save_analysis(
        self, client: _EventAtomicClient, analysis_id: str, status: str
    ) -> dict[str, Any]:
        return SupabasePaperTradeRepository(client).save_result(
            event_id="hays-fy2026-results",
            expectation_version=1,
            source_document_id=None,
            analysis_id=analysis_id,
            result=PostReleasePaperResult(status, "result"),
        )

    def test_different_analyses_competing_for_terminal_event_have_one_winner(self) -> None:
        client = _EventAtomicClient()
        analysis_a = "00000000-0000-0000-0000-000000000002"
        analysis_b = "00000000-0000-0000-0000-000000000003"
        winner_a = self.save_analysis(client, analysis_a, "paper_executed")
        winner_b = self.save_analysis(client, analysis_b, "paper_executed")
        self.assertEqual(winner_a["analysis_id"], analysis_a)
        self.assertEqual(winner_b["analysis_id"], analysis_a)
        self.assertEqual(len([row for row in client.rows if row["status"] in TERMINAL]), 1)

    def test_existing_paper_execution_owns_event_against_new_analysis(self) -> None:
        client = _EventAtomicClient()
        analysis_a = "00000000-0000-0000-0000-000000000002"
        analysis_b = "00000000-0000-0000-0000-000000000003"
        self.save_analysis(client, analysis_a, "paper_executed")
        winner = self.save_analysis(client, analysis_b, "paper_executed")
        self.assertEqual(winner["analysis_id"], analysis_a)
        self.assertFalse(any(row["analysis_id"] == analysis_b for row in client.rows))

    def test_existing_expiry_owns_event_against_new_analysis(self) -> None:
        client = _EventAtomicClient()
        analysis_a = "00000000-0000-0000-0000-000000000002"
        analysis_b = "00000000-0000-0000-0000-000000000003"
        self.save_analysis(client, analysis_a, "expired_no_trade")
        winner = self.save_analysis(client, analysis_b, "paper_executed")
        self.assertEqual(winner["analysis_id"], analysis_a)
        self.assertEqual(winner["status"], "expired_no_trade")
        self.assertEqual(len([row for row in client.rows if row["status"] in TERMINAL]), 1)

    def test_single_analysis_waiting_to_paper_execution_still_works(self) -> None:
        client = _EventAtomicClient()
        analysis_id = "00000000-0000-0000-0000-000000000002"
        waiting = self.save_analysis(client, analysis_id, "waiting_confirmation")
        executed = self.save_analysis(client, analysis_id, "paper_executed")
        self.assertEqual(waiting["status"], "waiting_confirmation")
        self.assertEqual(executed["status"], "paper_executed")
        self.assertEqual(len(client.rows), 1)

    def test_event_claim_allows_only_one_analysis_owner(self) -> None:
        client = _EventAtomicClient()
        repository = SupabasePaperTradeRepository(client)
        owner_a = repository.claim_event(
            event_id="hays-fy2026-results", analysis_id="analysis-a"
        )
        owner_b = repository.claim_event(
            event_id="hays-fy2026-results", analysis_id="analysis-b"
        )
        self.assertEqual(owner_a["analysis_id"], "analysis-a")
        self.assertEqual(owner_b["analysis_id"], "analysis-a")


if __name__ == "__main__":
    unittest.main()
