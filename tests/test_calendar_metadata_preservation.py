import unittest
from datetime import date

from trading_system.calendar_provider import CalendarCandidate
from trading_system.calendar_repository import InMemoryCalendarEventRepository


def candidate(*, company_name: str, market: str) -> CalendarCandidate:
    return CalendarCandidate(
        company_name=company_name,
        instrument="DKS",
        market=market,
        event_type="earnings",
        scheduled_date=date(2026, 8, 24),
        source="finnhub",
        occurrence_key="2026Q2",
    )


class CalendarMetadataPreservationTests(unittest.TestCase):
    def test_enriched_metadata_survives_later_placeholder_fallback(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted_id = repo.sync_candidates(
            [candidate(company_name="DICK'S Sporting Goods, Inc.", market="USA")],
            source="finnhub",
        ).inserted[0]

        repo.sync_candidates(
            [candidate(company_name="DKS", market="Unknown")],
            source="finnhub",
        )

        stored = repo.get(inserted_id)
        assert stored is not None
        self.assertEqual(stored.company_name, "DICK'S Sporting Goods, Inc.")
        self.assertEqual(stored.market, "USA")

    def test_placeholder_metadata_is_improved_by_later_enrichment(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted_id = repo.sync_candidates(
            [candidate(company_name="DKS", market="Unknown")],
            source="finnhub",
        ).inserted[0]

        repo.sync_candidates(
            [candidate(company_name="DICK'S Sporting Goods, Inc.", market="USA")],
            source="finnhub",
        )

        stored = repo.get(inserted_id)
        assert stored is not None
        self.assertEqual(stored.company_name, "DICK'S Sporting Goods, Inc.")
        self.assertEqual(stored.market, "USA")

    def test_real_non_placeholder_correction_still_updates_candidate(self) -> None:
        repo = InMemoryCalendarEventRepository()
        inserted_id = repo.sync_candidates(
            [candidate(company_name="Old Name", market="USA")],
            source="finnhub",
        ).inserted[0]

        repo.sync_candidates(
            [candidate(company_name="Corrected Name", market="NASDAQ")],
            source="finnhub",
        )

        stored = repo.get(inserted_id)
        assert stored is not None
        self.assertEqual(stored.company_name, "Corrected Name")
        self.assertEqual(stored.market, "NASDAQ")


if __name__ == "__main__":
    unittest.main()
