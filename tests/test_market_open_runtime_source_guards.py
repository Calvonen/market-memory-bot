from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MarketOpenRuntimeSourceGuardTests(unittest.TestCase):
    def test_systemd_runs_event_kind_dispatcher(self) -> None:
        service = (ROOT / "deploy/systemd/marketai-approved-paper.service").read_text()
        self.assertIn("-m trading_system.approved_paper_dispatch_worker", service)
        self.assertNotIn("-m trading_system.approved_tracked_paper_worker\n", service)

    def test_systemd_gates_dispatcher_on_market_open_schema(self) -> None:
        service = (ROOT / "deploy/systemd/marketai-approved-paper.service").read_text()
        self.assertIn("ExecStartPre=", service)
        self.assertIn("scripts/verify_market_open_runtime_schema.py", service)
        gate = (ROOT / "scripts/verify_market_open_runtime_schema.py").read_text()
        self.assertIn("verify_market_open_runtime_schema", gate)
        self.assertIn("market_open_shell_trigger_exists", gate)
        self.assertIn("freeze_market_open_evidence_function_exists", gate)

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

    def test_market_open_runtime_verifier_checks_shell_trigger_and_freeze_rpc(self) -> None:
        migration = (
            ROOT
            / "supabase/migrations/20260903200000_market_open_strategy_shell_and_evidence.sql"
        ).read_text()
        self.assertIn("verify_market_open_runtime_schema", migration)
        self.assertIn("ensure_market_open_strategy_shell(uuid)", migration)
        self.assertIn("tracked_market_events_market_open_shell_after_date_write", migration)
        self.assertIn("freeze_market_open_evidence(uuid,integer,text,jsonb)", migration)

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
        self.assertIn("execution_price=latest.close_price", source)
        self.assertIn("pattern.execution_price", source)
        self.assertIn("resolved eToro symbol differs from canonical instrument", source)

    def test_latest_reaction_must_confirm_market_open_direction(self) -> None:
        source = (ROOT / "trading_system/market_open_paper.py").read_text()
        self.assertIn('if _direction(latest.return_pct) == "positive"', source)
        self.assertIn('if _direction(latest.return_pct) != "negative"', source)
        self.assertIn("reaction_pct=latest.return_pct", source)

    def test_frozen_market_open_evidence_persists_execution_price(self) -> None:
        source = (ROOT / "trading_system/market_open_evidence.py").read_text()
        self.assertIn('"execution_price": str(pattern.execution_price)', source)
        self.assertIn('pattern_payload["execution_price"]', source)
        self.assertIn("execution price does not match the confirming reaction", source)
        self.assertIn("execution_price=execution_price", source)

    def test_existing_frozen_evidence_verifies_hash_and_analysis_payload(self) -> None:
        source = (ROOT / "trading_system/market_open_paper_orchestration.py").read_text()
        self.assertIn('select("id,event_id,source_type,content_sha256,raw_text")', source)
        self.assertIn("hashlib.sha256(raw_text.encode(\"utf-8\")).hexdigest()", source)
        self.assertIn("persisted_analysis != _analysis_payload(pattern)", source)
        self.assertIn('row.get("raw_response")', source)
        self.assertIn("raw response disagrees with source document", source)

    def test_market_open_execution_expires_frozen_price_and_newer_reactions(self) -> None:
        source = (ROOT / "trading_system/market_open_paper_orchestration.py").read_text()
        self.assertIn("_MAX_FROZEN_EXECUTION_AGE = timedelta(minutes=2)", source)
        self.assertIn("live_latest.candle_start", source)
        self.assertIn("frozen_latest.candle_start", source)
        self.assertIn("current <= completed_at + _MAX_FROZEN_EXECUTION_AGE", source)
        self.assertIn("expired or was superseded by a newer 1m reaction", source)

    def test_recovery_precedes_freshness_and_boundary_rechecks_live_reactions(self) -> None:
        source = (ROOT / "trading_system/market_open_paper_orchestration.py").read_text()
        claim = source.index("claim = _claim_event_for_task(")
        freshness = source.index("execution_now = now or datetime.now(UTC)", claim)
        self.assertLess(claim, freshness)
        self.assertIn("class _BrokerBoundaryFreshnessGuard", source)
        self.assertIn("def recheck_before_broker_attempt()", source)
        self.assertIn("tracked_events.list_reactions(event.event_id)", source)
        self.assertIn("_with_broker_boundary_freshness(", source)

    def test_market_open_execution_requires_positive_event_cap(self) -> None:
        source = (ROOT / "trading_system/market_open_paper_orchestration.py").read_text()
        self.assertIn("execution_context.max_position_value_usd", source)
        self.assertIn("not math.isfinite(event_cap)", source)
        self.assertIn("event_cap <= 0", source)
        self.assertIn("requires a finite positive per-event position cap", source)


if __name__ == "__main__":
    unittest.main()
