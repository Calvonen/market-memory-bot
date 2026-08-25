from pathlib import Path
import unittest


class PreEventContextSameDayGateSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902097000_pre_event_context_allow_same_day_session.sql"
        ).read_text(encoding="utf-8")
        base_start = cls.sql.index(
            "create or replace function public.capture_tracked_market_event_pre_event_context(\n"
        )
        validated_start = cls.sql.index(
            "create or replace function "
            "public.capture_tracked_market_event_pre_event_context_validated(\n"
        )
        cls.base = cls.sql[base_start:validated_start]
        cls.validated = cls.sql[
            validated_start : cls.sql.index(
                "create or replace function public.tracked_event_runtime_schema_version()"
            )
        ]

    # --- base RPC keeps the strict rule -------------------------------------

    def test_base_rpc_still_rejects_any_same_day_session(self) -> None:
        # Direct/legacy service_role callers have not proven the close time, so
        # the same-day allowance must not be reachable through the base RPC.
        self.assertIn("if snapshot_session_date >= event_local_date then", self.base)
        self.assertNotIn("if snapshot_session_date > event_local_date then", self.base)
        self.assertIn("pre_event_market_context_not_before_event", self.base)

    def test_base_rpc_takes_no_session_close_proof(self) -> None:
        self.assertNotIn("input_session_close", self.base)

    def test_base_rpc_keeps_ordering_structure_and_lifecycle_guards(self) -> None:
        for guard in (
            "if not public.is_valid_pre_event_market_context_v1(input_pre_event_market_context) then",
            "if snapshot_previous_session_date >= snapshot_session_date then",
            "tracked_market_event_not_found",
            "tracked_market_event_pre_event_context_locked",
            "tracked_market_event_not_context_captureable",
            "for update;",
        ):
            self.assertIn(guard, self.base)

    def test_base_rpc_retains_exact_retry_idempotency(self) -> None:
        retry = self.base.index(
            "if existing_row.pre_event_market_context = input_pre_event_market_context then"
        )
        write = self.base.index("set pre_event_market_context = input_pre_event_market_context,")
        self.assertLess(retry, write)
        self.assertIn("return existing_row;", self.base)

    # --- validated RPC carries the relaxation behind a proof ----------------

    def test_validated_rpc_permits_same_day_with_a_strict_greater_than(self) -> None:
        self.assertIn("if snapshot_session_date > event_local_date then", self.validated)
        self.assertNotIn("if snapshot_session_date >= event_local_date then", self.validated)

    def test_validated_rpc_requires_the_session_close_proof(self) -> None:
        self.assertIn("input_session_close timestamptz", self.validated)
        self.assertIn("if input_session_close is null then", self.validated)
        self.assertIn("input_session_close is required", self.validated)

    def test_validated_rpc_checks_the_proof_against_facts_the_database_owns(self) -> None:
        # event_at comes from the locked row and the clock from the database, so
        # neither ordering can be asserted by the caller alone.
        self.assertIn("if input_session_close > existing_row.event_at then", self.validated)
        self.assertIn("pre_event_market_context_session_not_closed_before_event", self.validated)
        self.assertIn(
            "if input_session_close > pg_catalog.clock_timestamp() then", self.validated
        )
        self.assertIn("pre_event_market_context_session_not_closed_yet", self.validated)

    def test_validated_rpc_binds_the_proof_to_the_snapshot_session(self) -> None:
        # Without this an unrelated older close could stand in as proof.
        self.assertIn(
            "session_close_local_date := (input_session_close at time zone "
            "input_market_timezone)::date;",
            self.validated,
        )
        self.assertIn("if session_close_local_date <> snapshot_session_date then", self.validated)
        self.assertIn("pre_event_market_context_session_close_mismatch", self.validated)

    def test_validated_rpc_proof_gates_precede_the_write(self) -> None:
        closed_before_event = self.validated.index(
            "if input_session_close > existing_row.event_at then"
        )
        closed_yet = self.validated.index(
            "if input_session_close > pg_catalog.clock_timestamp() then"
        )
        write = self.validated.index(
            "set pre_event_market_context = input_pre_event_market_context,"
        )
        self.assertLess(closed_before_event, write)
        self.assertLess(closed_yet, write)

    def test_validated_rpc_keeps_version_deadline_and_freeze_invariants(self) -> None:
        lock = self.validated.index("for update;")
        deadline = self.validated.index(
            "if pg_catalog.clock_timestamp() >= existing_row.event_at then"
        )
        version = self.validated.index(
            "if existing_row.updated_at is distinct from input_expected_updated_at then"
        )
        self.assertLess(lock, deadline)
        self.assertLess(deadline, version)
        self.assertIn("tracked_market_event_pre_event_context_deadline_passed", self.validated)
        self.assertIn("tracked_market_event_version_conflict", self.validated)
        self.assertIn("tracked_market_event_pre_event_context_locked", self.validated)
        self.assertIn("tracked_market_event_not_context_captureable", self.validated)

    def test_validated_rpc_exact_retry_precedes_every_gate(self) -> None:
        # A replayed capture whose response was lost must not have to re-prove
        # timing that was already accepted, and creates no new snapshot.
        retry = self.validated.index(
            "if existing_row.pre_event_market_context = input_pre_event_market_context then"
        )
        deadline = self.validated.index(
            "if pg_catalog.clock_timestamp() >= existing_row.event_at then"
        )
        version = self.validated.index(
            "if existing_row.updated_at is distinct from input_expected_updated_at then"
        )
        proof = self.validated.index("if input_session_close > existing_row.event_at then")
        self.assertLess(retry, deadline)
        self.assertLess(retry, version)
        self.assertLess(retry, proof)
        self.assertIn("return existing_row;", self.validated)

    def test_validated_rpc_is_security_invoker_and_service_role_only(self) -> None:
        self.assertIn("security invoker", self.validated)
        self.assertNotIn("security definer", self.sql)
        self.assertIn(
            "revoke all on function "
            "public.capture_tracked_market_event_pre_event_context_validated(\n"
            "  uuid, jsonb, text, text, timestamptz, timestamptz\n"
            ") from public;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function "
            "public.capture_tracked_market_event_pre_event_context_validated(\n"
            "  uuid, jsonb, text, text, timestamptz, timestamptz\n"
            ") to service_role;",
            self.sql,
        )

    # --- boundary documentation and deploy gate -----------------------------

    def test_trust_boundary_is_documented(self) -> None:
        self.assertIn("TRUST BOUNDARY", self.sql)
        self.assertIn("no exchange calendar", self.sql)
        self.assertIn("session_calendar_adapter.py", self.sql)

    def test_verifier_requires_the_validated_rpc(self) -> None:
        self.assertIn(
            "capture_tracked_market_event_pre_event_context_validated_function_exists boolean",
            self.sql,
        )
        self.assertIn(
            "public.capture_tracked_market_event_pre_event_context_validated"
            "(uuid,jsonb,text,text,timestamptz,timestamptz)",
            self.sql,
        )

    def test_verifier_still_checks_the_earlier_capture_chain(self) -> None:
        verifier = self.sql[
            self.sql.index("create function public.verify_tracked_event_runtime_schema()") :
        ]
        for signature in (
            "public.capture_tracked_market_event_pre_event_context(uuid,jsonb,text,text)",
            "public.capture_tracked_market_event_pre_event_context_if_current(",
            "public.validate_tracked_market_event_pre_event_context_if_current(",
            "public.fail_tracked_market_event_pre_event_deadline_if_current(",
        ):
            self.assertIn(signature, verifier)
        self.assertIn("public.tracked_event_runtime_schema_version();", verifier)

    def test_runtime_schema_version_is_six(self) -> None:
        version_fn = self.sql[
            self.sql.index(
                "create or replace function public.tracked_event_runtime_schema_version()"
            ) :
        ]
        self.assertIn("select 6;", version_fn[:200])

    def test_whole_migration_is_one_transaction(self) -> None:
        begin = self.sql.index("\nbegin;\n")
        first_create = self.sql.index("create or replace function")
        self.assertLess(begin, first_create)
        self.assertTrue(self.sql.rstrip().endswith("commit;"))


if __name__ == "__main__":
    unittest.main()
