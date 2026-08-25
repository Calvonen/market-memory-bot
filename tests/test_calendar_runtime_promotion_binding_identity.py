import unittest
from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20260902100000_calendar_runtime_promotion_atomic.sql"
)


class CalendarRuntimePromotionBindingIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MIGRATION.read_text(encoding="utf-8")

    def test_existing_binding_requires_canonical_calendar_external_key(self) -> None:
        expected = (
            "existing_runtime.external_key is distinct from "
            "('calendar:' || calendar_row.id::text)"
        )
        self.assertIn(expected, self.source)

        guard_index = self.source.index(expected)
        conflict_index = self.source.index(
            "calendar_runtime_binding_identity_conflict", guard_index
        )
        noop_index = self.source.index("noop_existing", guard_index)
        self.assertLess(guard_index, conflict_index)
        self.assertLess(conflict_index, noop_index)


if __name__ == "__main__":
    unittest.main()
