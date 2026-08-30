import unittest
from pathlib import Path


SCANNER = Path("mobile/src/app/(tabs)/scanner.tsx")
EDITOR = Path("mobile/src/components/tracking-profile-editor.tsx")
SUMMARY = Path("mobile/src/components/tracking-profile-summary.tsx")


class MobileTrackingProfileSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scanner = SCANNER.read_text(encoding="utf-8")
        cls.editor = EDITOR.read_text(encoding="utf-8")
        cls.summary = SUMMARY.read_text(encoding="utf-8")

    def test_summary_reads_only_canonical_tracking_profiles(self) -> None:
        self.assertIn("getTrackingProfiles(trackedInstrumentId)", self.summary)
        self.assertIn("from '@/services/tracking-profiles';", self.summary)
        self.assertNotIn("fetch(", self.summary)
        self.assertNotIn("setTrackingProfile", self.summary)

    def test_summary_renders_only_enabled_initial_profile_labels(self) -> None:
        self.assertIn("profiles.filter((profile) => profile.enabled)", self.summary)
        self.assertIn("earnings: 'Tulosjulkaisut'", self.summary)
        self.assertIn("trend: 'Trendi'", self.summary)
        self.assertIn("future_tech: 'Future Tech'", self.summary)
        self.assertIn("labels.join(' · ')", self.summary)
        self.assertNotIn("catalyst", self.summary)
        self.assertNotIn("anomaly", self.summary)

    def test_scanner_shows_summary_only_for_persisted_tracked_rows(self) -> None:
        self.assertIn("{trackedInstrument ? (", self.scanner)
        self.assertIn("<TrackingProfileSummary", self.scanner)
        self.assertIn("trackedInstrumentId={trackedInstrument.id}", self.scanner)

    def test_successful_editor_save_refreshes_card_summary(self) -> None:
        self.assertIn("onSaved?: (profile: TrackedInstrumentProfile) => void;", self.editor)
        self.assertIn("onSaved?.(saved);", self.editor)
        self.assertIn("onSaved={() => refreshProfileSummary(trackedInstrument.id)}", self.scanner)
        self.assertIn("key={`${trackedInstrument.id}:${profileRefreshVersion}`}", self.scanner)

    def test_summary_failures_do_not_break_scanner_rows(self) -> None:
        self.assertIn(".catch(() => {", self.summary)
        self.assertIn("Profile annotations are supplemental", self.summary)
        self.assertIn("if (!active) return;", self.summary)

    def test_profile_summary_does_not_create_events_or_trading_state(self) -> None:
        source = (self.scanner + self.editor + self.summary).lower()
        for forbidden in (
            "tracked-events",
            "calendar/",
            "trading-tasks",
            "strategyengine",
            "riskengine",
            "broker",
            "paper_order",
            "live-execution",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
