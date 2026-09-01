import unittest
from pathlib import Path


COMPONENT_PATH = Path("mobile/src/components/HomeTrackedCompaniesSection.tsx")


class MobileHomeCompanySearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = COMPONENT_PATH.read_text(encoding="utf-8")

    def test_home_search_reuses_existing_read_symbol_endpoint(self) -> None:
        self.assertIn("/api/v1/symbols?q=", self.source)
        self.assertIn("&limit=8", self.source)
        self.assertIn("apiGet<SymbolSearchResult[]>", self.source)

    def test_search_result_tracks_only_through_canonical_service(self) -> None:
        self.assertIn("const SEARCH_TRACKING_ACTOR = 'mobile-home-company-search';", self.source)
        self.assertIn("const saved = await trackInstrument(", self.source)
        self.assertIn("source: 'manual'", self.source)
        for forbidden in (
            "tracked-events",
            "trading-tasks",
            "strategy-draft",
            "paper-status",
            "live-execution",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_successful_add_opens_existing_profile_management(self) -> None:
        self.assertIn("setExpandedId(saved.id);", self.source)
        self.assertIn("<TrackingProfileEditor trackedInstrumentId={instrument.id} />", self.source)

    def test_search_market_mapping_matches_existing_scanner_countries(self) -> None:
        self.assertIn("if (ticker.endsWith('.HE')) return 'Finland';", self.source)
        self.assertIn("if (ticker.endsWith('.ST')) return 'Sweden';", self.source)
        self.assertIn("if (ticker.endsWith('.DE')) return 'Germany';", self.source)
        self.assertIn("if (!ticker.includes('.')) return 'USA';", self.source)

    def test_query_change_invalidates_stale_search_response(self) -> None:
        self.assertIn("latestSearchId.current += 1;", self.source)
        self.assertIn("onChangeText={changeSearchQuery}", self.source)
        self.assertIn("if (searchId !== latestSearchId.current) return;", self.source)


if __name__ == "__main__":
    unittest.main()
