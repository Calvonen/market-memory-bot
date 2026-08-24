from pathlib import Path
import unittest


class PreEventDeadlineFailGateSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902096000_pre_event_deadline_fail_gate.sql"
        ).read_text(encoding="utf-8")

    def test_function_is_security_invoker(self) -> None:
        self.assertIn(
            "create or replace function "
            "public.fail_tracked_market_event_pre_event_deadline_if_current(",
            self.sql,
        )
        self.assertIn("security invoker", self.sql)
        self.assertNotIn("security definer", self.sql)

    def test_execute_is_revoked_from_public_and_granted_only_to_service_role(self) -> None:
        self.assertIn(
            "revoke all on function "
            "public.fail_tracked_market_event_pre_event_deadline_if_current(\n"
            "  uuid, timestamptz, text, text\n"
            ") from public;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function "
            "public.fail_tracked_market_event_pre_event_deadline_if_current(\n"
            "  uuid, timestamptz, text, text\n"
            ") to service_role;",
            self.sql,
        )

    def test_row_lock_precedes_version_check_which_precedes_the_deadline_check(self) -> None:
        lock = self.sql.index("for update;")
        version_check = self.sql.index(
            "if existing_row.updated_at is distinct from input_expected_updated_at then"
        )
        state_check = self.sql.index("if existing_row.status <> 'tracked'")
        deadline_check = self.sql.index(
            "if pg_catalog.clock_timestamp() < existing_row.event_at then"
        )
        self.assertLess(lock, version_check)
        self.assertLess(version_check, state_check)
        self.assertLess(state_check, deadline_check)

    def test_a_persisted_context_makes_the_event_not_deadline_failable(self) -> None:
        # A committed capture proves the preparation this failure is about
        # succeeded, even when the caller only saw an exception (lost response,
        # or the acquisition thread raising after the capture committed). The
        # guard must therefore require a null context alongside the other two
        # conditions, so FAILED can never be written over a prepared event.
        guard_start = self.sql.index("if existing_row.status <> 'tracked'")
        guard_end = self.sql.index("then", guard_start) + len("then")
        guard_text = " ".join(self.sql[guard_start:guard_end].split())
        self.assertEqual(
            guard_text,
            "if existing_row.status <> 'tracked' "
            "or existing_row.reference_price is not null "
            "or existing_row.pre_event_market_context is not null then",
        )

    def test_context_guard_precedes_the_deadline_check_and_the_write(self) -> None:
        # Order matters: a prepared event must be rejected as not-failable
        # rather than reaching the deadline comparison or the terminal write.
        context_guard = self.sql.index(
            "or existing_row.pre_event_market_context is not null then"
        )
        deadline_check = self.sql.index(
            "if pg_catalog.clock_timestamp() < existing_row.event_at then"
        )
        terminal_write = self.sql.index("set status = 'failed',")
        self.assertLess(context_guard, deadline_check)
        self.assertLess(context_guard, terminal_write)

    def test_every_guard_precedes_the_terminal_write(self) -> None:
        deadline_check = self.sql.index(
            "if pg_catalog.clock_timestamp() < existing_row.event_at then"
        )
        terminal_write = self.sql.index("set status = 'failed',")
        self.assertLess(deadline_check, terminal_write)
        self.assertIn("tracked_market_event_pre_event_deadline_not_reached", self.sql)
        self.assertIn("tracked_market_event_not_pre_event_failable", self.sql)
        self.assertIn("tracked_market_event_version_conflict", self.sql)

    def test_deadline_is_read_from_the_locked_row_not_from_an_input(self) -> None:
        # The caller never supplies an event_at - the locked row is the only
        # authority on whether the deadline actually passed.
        signature_end = self.sql.index(")\nreturns public.tracked_market_events")
        signature = self.sql[: signature_end + 1]
        self.assertNotIn("input_event_at", signature)
        self.assertIn("existing_row.event_at", self.sql)

    def test_whole_migration_is_one_transaction(self) -> None:
        # The RPC, the version bump and the verifier rebuild must land together
        # or not at all: a partial apply could report version 5 from a database
        # without the RPC the version is supposed to certify.
        begin = self.sql.index("\nbegin;\n")
        first_create = self.sql.index("create or replace function")
        self.assertLess(begin, first_create)
        self.assertTrue(self.sql.rstrip().endswith("commit;"))

    def test_runtime_schema_version_is_bumped_to_five(self) -> None:
        version_fn = self.sql[
            self.sql.index(
                "create or replace function public.tracked_event_runtime_schema_version()"
            ) :
        ]
        self.assertIn("select 5;", version_fn[:200])

    def test_verifier_checks_the_new_deadline_fail_rpc(self) -> None:
        self.assertIn(
            "public.fail_tracked_market_event_pre_event_deadline_if_current(uuid,timestamptz,text,text)",
            self.sql,
        )
        self.assertIn(
            "fail_tracked_market_event_pre_event_deadline_if_current_function_exists boolean",
            self.sql,
        )

    def test_verifier_still_checks_every_earlier_context_rpc(self) -> None:
        verifier = self.sql[
            self.sql.index("create function public.verify_tracked_event_runtime_schema()") :
        ]
        for signature in (
            "public.upsert_tracked_market_event(",
            "public.arm_tracked_market_event_resolution(",
            "public.capture_tracked_market_event_reference(",
            "public.capture_tracked_market_event_reaction_anchor(",
            "public.capture_tracked_market_event_config_snapshot(",
            "public.capture_tracked_market_event_pre_event_context(",
            "public.capture_tracked_market_event_pre_event_context_if_current(",
            "public.validate_tracked_market_event_pre_event_context_if_current(",
        ):
            self.assertIn(signature, verifier)
        self.assertIn("public.tracked_event_runtime_schema_version();", verifier)


if __name__ == "__main__":
    unittest.main()
