from __future__ import annotations

import unittest
from dataclasses import dataclass

from trading_system.models import TradingMode
from trading_system.trading_task import TradingTaskState
from trading_system.trading_task_repository import SupabaseTradingTaskRepository


@dataclass
class Response:
    data: object


class Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return Response(self.rows)


class Rpc:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return Response(self.response)


class Client:
    def __init__(self, *, rows=None, rpc_rows=None):
        self.rows = rows or []
        self.rpc_rows = rpc_rows or {}
        self.calls = []

    def table(self, name):
        self.calls.append(("table", name))
        return Query(self.rows)

    def rpc(self, name, params):
        self.calls.append(("rpc", name, params))
        return Rpc(self.rpc_rows.get(name, []))


def row(*, state="pending", mode="PAPER"):
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "tracked_event_id": "22222222-2222-2222-2222-222222222222",
        "source_event_id": "tracked:22222222-2222-2222-2222-222222222222",
        "instrument": "EXM.ASX",
        "mode": mode,
        "state": state,
        "created_by": "tester",
        "created_at": "2026-08-30T18:00:00+00:00",
        "approved_by": "approver" if state == "approved" else None,
        "approved_at": "2026-08-30T18:01:00+00:00" if state == "approved" else None,
        "cancelled_by": "canceller" if state == "cancelled" else None,
        "cancelled_at": "2026-08-30T18:02:00+00:00" if state == "cancelled" else None,
    }


class TradingTaskRepositoryTests(unittest.TestCase):
    def test_create_pending_uses_control_rpc(self) -> None:
        client = Client(rpc_rows={"create_trading_task": [row()]})
        repo = SupabaseTradingTaskRepository(client)
        created = repo.create_pending(
            tracked_event_id="22222222-2222-2222-2222-222222222222",
            source_event_id="tracked:22222222-2222-2222-2222-222222222222",
            instrument="EXM.ASX",
            mode=TradingMode.PAPER,
            actor="tester",
        )
        self.assertIs(created.state, TradingTaskState.PENDING)
        self.assertEqual(client.calls[0][1], "create_trading_task")

    def test_pending_task_cannot_project_execution_authority(self) -> None:
        repo = SupabaseTradingTaskRepository(Client(rows=[row()]))
        with self.assertRaisesRegex(ValueError, "not approved"):
            repo.execution_context("11111111-1111-1111-1111-111111111111")

    def test_approved_task_projects_exact_execution_authority(self) -> None:
        repo = SupabaseTradingTaskRepository(Client(rows=[row(state="approved")]))
        context = repo.execution_context("11111111-1111-1111-1111-111111111111")
        self.assertEqual(context.task_id, "11111111-1111-1111-1111-111111111111")
        self.assertEqual(
            context.source_event_id,
            "tracked:22222222-2222-2222-2222-222222222222",
        )
        self.assertEqual(context.instrument, "EXM.ASX")
        self.assertIs(context.mode, TradingMode.PAPER)

    def test_cancelled_task_cannot_project_execution_authority(self) -> None:
        repo = SupabaseTradingTaskRepository(Client(rows=[row(state="cancelled")]))
        with self.assertRaisesRegex(ValueError, "not approved"):
            repo.execution_context("11111111-1111-1111-1111-111111111111")

    def test_ambiguous_task_read_fails_closed(self) -> None:
        repo = SupabaseTradingTaskRepository(Client(rows=[row(), row()]))
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            repo.get("11111111-1111-1111-1111-111111111111")

    def test_malformed_row_fails_closed(self) -> None:
        bad = row(state="approved")
        bad["approved_by"] = None
        repo = SupabaseTradingTaskRepository(Client(rows=[bad]))
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            repo.get("11111111-1111-1111-1111-111111111111")


if __name__ == "__main__":
    unittest.main()
