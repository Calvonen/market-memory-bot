from pathlib import Path
import unittest


class PreEventContextSameDayGateSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902097000_pre_event_context_allow_same_day_session.sql"
        ).read_text(encoding="utf-8")

    def test_same_day_session_is_permitted_by_a_strict_greater_than(self) -> None:
        # The relaxation is exactly ">=" becoming ">": a session dated after the
        # event's local market date is still rejected, a same-day one is not.
        self.assertIn(
            "if snapshot_session_date > event_local_date then", self.sql
        )
        self.assertNotIn("if snapshot_session_date >= event_local_date then", self.sql)
        self.assertNotIn("snapshot_session_date >= event_trading_date", self.sql)
        self.assertIn("pre_event_market_context_not_before_event", self.sql)

    def test_previous_session_ordering_is_still_enforced(self) -> None:
        self.assertIn(
            "if snapshot_previous_session_date >= snapshot_session_date then", self.sql
        )
        self.assertIn("pre_event_market_context_sessions_out_of_order", self.sql)

    def test_previous_session_must_still_precede_the_event(self) -> None:
        # Only the latest session may fall on the event's own date.
        self.assertIn(
            "if snapshot_previous_session_date >= event_local_date then", self.sql
        )

    def test_structural_validator_and_lifecycle_guards_are_retained(self) -> None:
        for guard in (
            "if not public.is_valid_pre_event_market_context_v1(input_pre_event_market_context) then",
            "invalid_pre_event_market_context",
            "input_market_timezone is required",
            "input_actor is required",
            "tracked_market_event_not_found",
            "tracked_market_event_pre_event_context_locked",
            "tracked_market_event_not_context_captureable",
        ):
            self.assertIn(guard, self.sql)

    def test_exact_retry_idempotency_is_retained(self) -> None:
        retry = self.sql.index(
            "if existing_row.pre_event_market_context = input_pre_event_market_context then"
        )
        conflict = self.sql.index("tracked_market_event_pre_event_context_locked")
        write = self.sql.index("set pre_event_market_context = input_pre_event_market_context,")
        self.assertLess(retry, conflict)
        self.assertLess(retry, write)
        self.assertIn("return existing_row;", self.sql)

    def test_row_is_locked_before_validation_against_it(self) -> None:
        lock = self.sql.index("for update;")
        local_date = self.sql.index(
            "event_local_date := (existing_row.event_at at time zone input_market_timezone)::date;"
        )
        self.assertLess(lock, local_date)

    def test_function_stays_security_invoker_and_service_role_only(self) -> None:
        self.assertIn("security invoker", self.sql)
        self.assertNotIn("security definer", self.sql)
        self.assertIn(
            "revoke all on function public.capture_tracked_market_event_pre_event_context "
            "from public;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.capture_tracked_market_event_pre_event_context "
            "to service_role;",
            self.sql,
        )

    def test_trust_boundary_is_documented(self) -> None:
        # The database cannot know session close times, so the comment must say
        # who proves the timing instead of leaving the relaxation unexplained.
        self.assertIn("TRUST BOUNDARY", self.sql)
        self.assertIn("no exchange calendar", self.sql)
        self.assertIn("acquire_and_persist_pre_event_market_context_for_event", self.sql)

    def test_runtime_schema_version_is_bumped_to_six(self) -> None:
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
