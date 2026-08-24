from pathlib import Path
import re
import unittest


class TrackedEventEtoroMarketPrivilegeMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = Path(
            "supabase/migrations/20260902091000_tracked_event_etoro_market_capture.sql"
        ).read_text(encoding="utf-8")

    def test_service_role_cannot_directly_update_resolved_market(self) -> None:
        self.assertIn(
            "revoke update on table public.tracked_market_events from service_role;",
            self.sql,
        )
        self.assertNotRegex(
            self.sql,
            re.compile(
                r"grant\s+update\s+on\s+(?:table\s+)?public\.tracked_market_events\s+to\s+service_role\s*;",
                re.IGNORECASE,
            ),
        )
        self.assertNotRegex(
            self.sql,
            re.compile(
                r"grant\s+update\s*\([^;)]*\bresolved_etoro_market\b[^;)]*\)\s*"
                r"on\s+(?:table\s+)?public\.tracked_market_events\s+to\s+service_role\s*;",
                re.IGNORECASE,
            ),
        )

        grant_builder = re.search(
            r"select\s+string_agg\(pg_catalog\.format\('%I',\s*a\.attname\),"
            r".*?from\s+pg_catalog\.pg_attribute\s+a"
            r".*?where\s+a\.attrelid\s*=\s*'public\.tracked_market_events'::regclass"
            r"(?P<predicates>.*?)"
            r"execute\s+pg_catalog\.format\(\s*"
            r"'grant update \(%s\) on table public\.tracked_market_events to service_role'",
            self.sql,
            re.IGNORECASE | re.DOTALL,
        )
        self.assertIsNotNone(grant_builder)
        assert grant_builder is not None
        self.assertRegex(
            grant_builder.group("predicates"),
            re.compile(
                r"and\s+a\.attname\s*<>\s*'resolved_etoro_market'\s*;",
                re.IGNORECASE,
            ),
        )

    def test_capture_rpc_is_security_definer_with_locked_search_path_and_acl(self) -> None:
        capture_header = re.search(
            r"create\s+or\s+replace\s+function\s+public\.capture_tracked_market_event_resolved_market\s*\("
            r".*?\)\s*"
            r"returns\s+public\.tracked_market_events\s*"
            r"language\s+plpgsql\s*"
            r"security\s+definer\s*"
            r"set\s+search_path\s*=\s*pg_catalog\s*,\s*public\s*"
            r"as\s+\$\$",
            self.sql,
            re.IGNORECASE | re.DOTALL,
        )
        self.assertIsNotNone(capture_header)

        signature = (
            r"public\.capture_tracked_market_event_resolved_market\s*\(\s*"
            r"uuid\s*,\s*bigint\s*,\s*text\s*,\s*text\s*,\s*text\s*,\s*text\s*\)"
        )
        self.assertRegex(
            self.sql,
            re.compile(
                rf"revoke\s+all\s+on\s+function\s+{signature}\s+from\s+public\s*;",
                re.IGNORECASE,
            ),
        )
        self.assertRegex(
            self.sql,
            re.compile(
                rf"grant\s+execute\s+on\s+function\s+{signature}\s+to\s+service_role\s*;",
                re.IGNORECASE,
            ),
        )

    def test_resolved_market_trigger_guards_insert_and_update_on_target_table(self) -> None:
        self.assertRegex(
            self.sql,
            re.compile(
                r"create\s+trigger\s+guard_tracked_market_event_resolved_market\s*"
                r"before\s+insert\s+or\s+update\s+of\s+resolved_etoro_market\s*"
                r"on\s+public\.tracked_market_events\s*"
                r"for\s+each\s+row\s*"
                r"execute\s+function\s+public\.guard_tracked_market_event_resolved_market\s*\(\s*\)\s*;",
                re.IGNORECASE | re.DOTALL,
            ),
        )
        self.assertIn(
            "tracked_market_event_resolved_market_immutable",
            self.sql,
        )


if __name__ == "__main__":
    unittest.main()
