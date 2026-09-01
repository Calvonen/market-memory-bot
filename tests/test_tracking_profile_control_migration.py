import re
import unittest
from pathlib import Path


MIGRATION_PATH = Path(
    "supabase/migrations/20260901123100_tracked_instrument_profile_control.sql"
)


def _function_body(sql: str, signature: str) -> str:
    start = sql.index(signature)
    body_start = sql.index("as $$", start)
    end = sql.index("\n$$;", body_start)
    return sql[body_start:end].lower()


class TrackedInstrumentProfileControlMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION_PATH.read_text(encoding="utf-8")
        cls.lower_sql = cls.sql.lower()

    def test_upsert_is_service_role_rpc_only(self) -> None:
        signature = (
            "public.upsert_tracked_instrument_profile(text, text, text, boolean, text)"
        )
        self.assertIn(
            "create or replace function public.upsert_tracked_instrument_profile(",
            self.lower_sql,
        )
        self.assertIn(f"revoke all on function {signature}", self.lower_sql)
        self.assertIn(f"grant execute on function {signature}", self.lower_sql)
        self.assertIn("to service_role", self.lower_sql)

    def test_upsert_is_bounded_to_profile_table_only(self) -> None:
        body = _function_body(
            self.sql,
            "create or replace function public.upsert_tracked_instrument_profile(",
        )
        dml_targets = [
            target
            for _verb, target in re.findall(
                r"(?im)^\s*(insert\s+into|update|delete\s+from|truncate(?:\s+table)?)\s+([a-z_][a-z0-9_.]*)",
                body,
            )
        ]
        self.assertEqual(dml_targets, ["public.tracked_instrument_profiles"])

        for forbidden in (
            "tracked_market_events",
            "calendar_events",
            "event_expectations",
            "strategy",
            "risk",
            "broker",
            "paper_trade",
            "trading_task",
        ):
            self.assertNotIn(forbidden, body)

    def test_upsert_validates_identity_type_specs_actor_and_enabled(self) -> None:
        for marker in (
            "tracked_profile_invalid_instrument_id",
            "tracked_profile_invalid_type",
            "tracked_profile_specs_too_long",
            "tracked_profile_invalid_actor",
            "tracked_profile_invalid_enabled",
            "tracked_profile_instrument_not_found",
        ):
            self.assertIn(marker, self.lower_sql)
        self.assertIn("length(normalized_specs) > 4000", self.lower_sql)
        self.assertIn("'earnings', 'trend', 'future_tech'", self.lower_sql)

    def test_upsert_preserves_profile_identity_and_updates_configuration(self) -> None:
        body = _function_body(
            self.sql,
            "create or replace function public.upsert_tracked_instrument_profile(",
        )
        self.assertIn(
            "on conflict (tracked_instrument_id, profile_type) do update",
            body,
        )
        self.assertIn("specs = excluded.specs", body)
        self.assertIn("enabled = excluded.enabled", body)
        self.assertIn("updated_by = excluded.updated_by", body)

    def test_schema_verifier_is_exposed(self) -> None:
        self.assertIn(
            "create or replace function public.verify_tracked_instrument_profile_schema()",
            self.lower_sql,
        )
        self.assertIn(
            "to_regprocedure('public.upsert_tracked_instrument_profile(text,text,text,boolean,text)')",
            self.lower_sql,
        )
        self.assertIn("select 1;", self.lower_sql)


if __name__ == "__main__":
    unittest.main()
