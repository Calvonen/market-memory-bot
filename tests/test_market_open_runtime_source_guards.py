from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MarketOpenRuntimeSourceGuardTests(unittest.TestCase):
    def test_systemd_runs_event_kind_dispatcher(self) -> None:
        service = (ROOT / "deploy/systemd/marketai-approved-paper.service").read_text()
        self.assertIn("-m trading_system.approved_paper_dispatch_worker", service)
        self.assertNotIn("-m trading_system.approved_tracked_paper_worker\n", service)

    def test_dispatcher_keeps_earnings_and_market_open_as_only_executable_kinds(self) -> None:
        source = (ROOT / "trading_system/approved_paper_dispatch_worker.py").read_text()
        self.assertIn('if normalized == "earnings"', source)
        self.assertIn('if normalized == "market_open"', source)
        self.assertIn("approved PAPER event kind is not executable", source)

    def test_evidence_freeze_uses_rule_engine_identity_and_extensions_digest(self) -> None:
        migration = (
            ROOT
            / "supabase/migrations/20260903200000_market_open_strategy_shell_and_evidence.sql"
        ).read_text()
        self.assertIn("freeze_market_open_evidence", migration)
        self.assertIn("'rule_engine'", migration)
        self.assertIn("'market-open-v1'", migration)
        self.assertIn("'market_open_reaction_evidence'", migration)
        self.assertIn("extensions.digest", migration)
        self.assertIn("existing_count > 1", migration)
        self.assertIn("return query select analysis_row.id, document_row.id, false", migration)

    def test_market_open_source_type_constraint_is_extended(self) -> None:
        migration = (
            ROOT
            / "supabase/migrations/20260903200000_market_open_strategy_shell_and_evidence.sql"
        ).read_text()
        self.assertIn("pg_get_expr(c.conbin, c.conrelid) ilike '%source_type%'", migration)
        self.assertIn("source_type = %L", migration)
        self.assertIn("'market_open_reaction_evidence'", migration)

    def test_market_open_shell_uses_jsonb_case_arrays(self) -> None:
        migration = (
            ROOT
            / "supabase/migrations/20260903200000_market_open_strategy_shell_and_evidence.sql"
        ).read_text()
        self.assertIn("'tracked:market_open:strategy-shell'", migration)
        self.assertIn("'{}'::jsonb", migration)
        self.assertIn("'[]'::jsonb", migration)
        self.assertGreaterEqual(migration.count("jsonb_build_array("), 4)
        self.assertIn("no earnings consensus or release evidence inferred", migration)

    def test_market_open_execution_uses_confirming_one_minute_price(self) -> None:
        source = (ROOT / "trading_system/market_open_paper.py").read_text()
        self.assertIn("execution_price=latest_positive.close_price", source)
        self.assertIn("execution_price=failure.close_price", source)
        self.assertIn("pattern.execution_price", source)
        self.assertIn("resolved eToro symbol differs from canonical instrument", source)


if __name__ == "__main__":
    unittest.main()
