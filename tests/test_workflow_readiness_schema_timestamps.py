from __future__ import annotations

import unittest

from trading_system.workflow_readiness_evidence_loader import (
    SupabaseWorkflowReadinessEvidenceLoader,
)


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table_name: str, rows: list[dict], calls: list[tuple]):
        self.table_name = table_name
        self.rows = rows
        self.calls = calls

    def select(self, fields: str):
        self.calls.append((self.table_name, "select", fields))
        return self

    def eq(self, field: str, value: str):
        self.calls.append((self.table_name, "eq", field, value))
        return self

    def order(self, field: str, desc: bool = False):
        self.calls.append((self.table_name, "order", field, desc))
        return self

    def limit(self, count: int):
        self.calls.append((self.table_name, "limit", count))
        return self

    def execute(self):
        return _Response(self.rows)


class _Client:
    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables
        self.calls: list[tuple] = []

    def table(self, name: str):
        return _Query(name, self.tables.get(name, []), self.calls)


class WorkflowReadinessSchemaTimestampTests(unittest.TestCase):
    def test_release_run_uses_checked_at_from_production_schema(self):
        client = _Client(
            {
                "event_ingestion_runs": [
                    {
                        "provider": "canonical_release_worker",
                        "status": "error",
                        "error_message": "action_required: source missing",
                        "checked_at": "2026-09-01T05:10:00+00:00",
                    }
                ]
            }
        )

        row = SupabaseWorkflowReadinessEvidenceLoader(client)._latest_release_run(
            "tracked:event"
        )

        self.assertIn(
            (
                "event_ingestion_runs",
                "select",
                "provider,status,error_message,checked_at",
            ),
            client.calls,
        )
        self.assertIn(
            ("event_ingestion_runs", "order", "checked_at", True),
            client.calls,
        )
        self.assertEqual(row["created_at"], "2026-09-01T05:10:00+00:00")

    def test_release_document_uses_fetched_at_from_production_schema(self):
        client = _Client(
            {
                "event_source_documents": [
                    {
                        "id": "doc-1",
                        "fetched_at": "2026-09-01T05:11:00+00:00",
                    }
                ]
            }
        )

        row = SupabaseWorkflowReadinessEvidenceLoader(client)._latest_release_document(
            "tracked:event"
        )

        self.assertIn(
            ("event_source_documents", "select", "id,fetched_at"),
            client.calls,
        )
        self.assertIn(
            ("event_source_documents", "order", "fetched_at", True),
            client.calls,
        )
        self.assertEqual(row["created_at"], "2026-09-01T05:11:00+00:00")


if __name__ == "__main__":
    unittest.main()
