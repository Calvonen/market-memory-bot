import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from trading_system.models import ComponentAssessment, Direction
from trading_system.paper_trade_repository import SupabasePaperTradeRepository
from trading_system.post_release_paper import PostReleasePaperResult


class _Query:
    def __init__(self, client: "_Client", table: str) -> None:
        self.client = client
        self.table = table
        self.payload: dict[str, Any] | None = None

    def upsert(self, payload: dict[str, Any], **kwargs: Any) -> "_Query":
        self.payload = payload
        self.client.upserts.append((payload, kwargs))
        return self

    def select(self, *_args: Any) -> "_Query":
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[self.payload or {}])


class _Client:
    def __init__(self) -> None:
        self.upserts: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def table(self, table: str) -> _Query:
        return _Query(self, table)


class PaperTradeRepositoryTests(unittest.TestCase):
    def test_waiting_result_persists_completed_components_and_deadline(self) -> None:
        client = _Client()
        repository = SupabasePaperTradeRepository(client)
        deadline = datetime(2026, 8, 20, 15, 45, tzinfo=UTC)
        result = PostReleasePaperResult(
            "waiting_confirmation",
            "technical confirmation is not aligned",
            completed_components=(
                ComponentAssessment("fundamental", Direction.LONG, 24, 35),
                ComponentAssessment("catalyst", Direction.LONG, 20, 25),
            ),
            confirmation_deadline_at=deadline,
        )

        repository.save_result(
            event_id="hays-fy2026-results",
            expectation_version=1,
            source_document_id="document-1",
            analysis_id="analysis-1",
            result=result,
        )

        payload, options = client.upserts[0]
        self.assertEqual(options["on_conflict"], "analysis_id")
        self.assertEqual(payload["confirmation_deadline_at"], deadline.isoformat())
        self.assertEqual(payload["completed_components"]["fundamental"]["score"], 24)
        self.assertEqual(payload["completed_components"]["catalyst"]["score"], 20)


if __name__ == "__main__":
    unittest.main()
