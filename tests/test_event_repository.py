import unittest
from datetime import date

from trading_system.event_repository import InMemoryEventExpectationRepository
from trading_system.models import EventExpectation


class EventExpectationRepositoryTests(unittest.TestCase):
    def test_save_and_reload_preserves_version_and_provenance(self) -> None:
        repo = InMemoryEventExpectationRepository()
        event = EventExpectation(
            event_id="hays-fy2026-results",
            instrument="HAS.L",
            event_name="Hays plc FY2026 results",
            scheduled_date=date(2026, 8, 20),
            consensus={"fy27_operating_profit_pre_exceptional_gbp_m": 55.6},
            source_name="Hays plc Analysts' Consensus",
            source_as_of=date(2026, 7, 1),
            version=1,
        )

        repo.save(event)
        loaded = repo.get(event.event_id)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.version, 1)
        self.assertEqual(loaded.consensus["fy27_operating_profit_pre_exceptional_gbp_m"], 55.6)
        self.assertEqual(loaded.source_as_of, date(2026, 7, 1))

    def test_upcoming_events_are_sorted_by_date(self) -> None:
        repo = InMemoryEventExpectationRepository()
        repo.save(EventExpectation("later", "B.L", "Later", date(2026, 9, 1)))
        repo.save(EventExpectation("earlier", "A.L", "Earlier", date(2026, 8, 20)))

        upcoming = repo.list_upcoming()

        self.assertEqual([event.event_id for event in upcoming], ["earlier", "later"])


if __name__ == "__main__":
    unittest.main()
