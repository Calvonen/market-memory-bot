import unittest
from datetime import date

from trading_system.event_repository import (
    EventExpectationNotFound,
    ExpectationPatch,
    InMemoryEventExpectationRepository,
)
from trading_system.models import EventExpectation


class EventExpectationRepositoryTests(unittest.TestCase):
    def test_reload_preserves_version_and_provenance(self) -> None:
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
        repo = InMemoryEventExpectationRepository(events={event.event_id: event})

        loaded = repo.get(event.event_id)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.version, 1)
        self.assertEqual(loaded.consensus["fy27_operating_profit_pre_exceptional_gbp_m"], 55.6)
        self.assertEqual(loaded.source_as_of, date(2026, 7, 1))

    def test_upcoming_events_are_sorted_by_date(self) -> None:
        repo = InMemoryEventExpectationRepository(
            events={
                "later": EventExpectation("later", "B.L", "Later", date(2026, 9, 1)),
                "earlier": EventExpectation("earlier", "A.L", "Earlier", date(2026, 8, 20)),
            }
        )

        upcoming = repo.list_upcoming()

        self.assertEqual([event.event_id for event in upcoming], ["earlier", "later"])

    # -- apply_partial_update(): patch resolved against a fresh read ------

    def test_apply_partial_update_merges_the_patch_onto_the_current_row(self) -> None:
        event = EventExpectation(
            event_id="hays-fy2026-results",
            instrument="HAS.L",
            event_name="Hays plc FY2026 results",
            scheduled_date=date(2026, 8, 20),
            consensus={"fy27_operating_profit_pre_exceptional_gbp_m": 55.6},
            triggers={"bull_fy27_operating_profit_gbp_m": 60.0},
            version=1,
        )
        repo = InMemoryEventExpectationRepository(events={event.event_id: event})

        updated = repo.apply_partial_update(
            "hays-fy2026-results",
            ExpectationPatch(triggers={"bull_fy27_operating_profit_gbp_m": 99.0}),
            change_note="raise bull threshold",
        )

        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.triggers["bull_fy27_operating_profit_gbp_m"], 99.0)
        # A field the patch never mentions must survive untouched.
        self.assertEqual(
            updated.consensus["fy27_operating_profit_pre_exceptional_gbp_m"], 55.6
        )
        self.assertEqual(repo.get("hays-fy2026-results").version, 2)

    def test_apply_partial_update_raises_for_an_unknown_event(self) -> None:
        repo = InMemoryEventExpectationRepository()

        with self.assertRaises(EventExpectationNotFound):
            repo.apply_partial_update(
                "does-not-exist", ExpectationPatch(), change_note="n/a"
            )


if __name__ == "__main__":
    unittest.main()
