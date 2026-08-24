from pathlib import Path
import re
import unittest


class TrackedEventUpsertFreezeGateSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902095000_pre_event_context_freeze_event_fields.sql"
        ).read_text(encoding="utf-8")

    def test_persisted_context_joins_the_existing_lock_guard(self) -> None:
        guard = re.search(
            r"if existing_row\.status <> 'tracked'\s*"
            r"or existing_row\.reference_price is not null\s*"
            r"or existing_row\.pre_event_market_context is not null then",
            self.sql,
        )
        self.assertIsNotNone(guard)

    def test_locked_fields_still_include_event_at_market_time_status_and_title(self) -> None:
        lock_block = self.sql[self.sql.index("tracked_market_event_locked") - 400 :]
        for field in (
            "existing_row.event_at is distinct from input_event_at",
            "existing_row.market is distinct from input_market",
            "existing_row.event_time_status is distinct from input_event_time_status",
            "existing_row.title is distinct from coalesce(input_title, '')",
        ):
            self.assertIn(field, lock_block[:400])

    def test_exact_matching_values_still_return_noop_locked_without_raising(self) -> None:
        raise_index = self.sql.index("raise exception 'tracked_market_event_locked';")
        noop_index = self.sql.index("'noop_locked'::text;")
        self.assertLess(raise_index, noop_index)

    def test_guard_is_exactly_the_three_known_lock_conditions(self) -> None:
        # TRACKED + no reference + no persisted context must still fall through
        # to the mutating update below, exactly like before this migration -
        # the freeze only adds pre_event_market_context to the existing OR, it
        # does not introduce any further restriction on ordinary edits.
        guard_start = self.sql.index("if existing_row.status <> 'tracked'")
        guard_end = self.sql.index("then", guard_start) + len("then")
        guard_text = " ".join(self.sql[guard_start:guard_end].split())
        self.assertEqual(
            guard_text,
            "if existing_row.status <> 'tracked' "
            "or existing_row.reference_price is not null "
            "or existing_row.pre_event_market_context is not null then",
        )

    def test_guard_precedes_the_mutating_update(self) -> None:
        guard_index = self.sql.index(
            "or existing_row.pre_event_market_context is not null then"
        )
        update_index = self.sql.index("update public.tracked_market_events\n  set company_name")
        self.assertLess(guard_index, update_index)

    def test_runtime_schema_version_is_bumped_to_four(self) -> None:
        version_fn = self.sql[
            self.sql.index("create or replace function public.tracked_event_runtime_schema_version()") :
        ]
        self.assertIn("select 4;", version_fn[:200])

    def test_verifier_checks_both_new_context_rpcs(self) -> None:
        self.assertIn(
            "public.capture_tracked_market_event_pre_event_context_if_current(uuid,jsonb,text,text,timestamptz)",
            self.sql,
        )
        self.assertIn(
            "public.validate_tracked_market_event_pre_event_context_if_current(uuid,timestamptz)",
            self.sql,
        )
        self.assertIn(
            "public.capture_tracked_market_event_pre_event_context(uuid,jsonb,text,text)",
            self.sql,
        )

    def test_verifier_still_returns_the_shared_runtime_version_marker(self) -> None:
        verifier = self.sql[
            self.sql.index("create function public.verify_tracked_event_runtime_schema()") :
        ]
        self.assertIn("public.tracked_event_runtime_schema_version();", verifier)


if __name__ == "__main__":
    unittest.main()
