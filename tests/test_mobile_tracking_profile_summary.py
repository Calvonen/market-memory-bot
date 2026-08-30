import unittest
from pathlib import Path


SCANNER = Path("mobile/src/app/(tabs)/scanner.tsx")
EDITOR = Path("mobile/src/components/tracking-profile-editor.tsx")
SUMMARY = Path("mobile/src/components/tracking-profile-summary.tsx")
SERVICE = Path("mobile/src/services/tracking-profiles.ts")


class MobileTrackingProfileSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scanner = SCANNER.read_text(encoding="utf-8")
        cls.editor = EDITOR.read_text(encoding="utf-8")
        cls.summary = SUMMARY.read_text(encoding="utf-8")
        cls.service = SERVICE.read_text(encoding="utf-8")

    def test_summary_is_display_only_and_scanner_owns_canonical_batch_read(self) -> None:
        self.assertNotIn("getTrackingProfiles", self.summary)
        self.assertNotIn("useEffect", self.summary)
        self.assertNotIn("fetch(", self.summary)
        self.assertNotIn("setTrackingProfile", self.summary)
        self.assertIn("getTrackingProfilesBatch", self.scanner)
        self.assertIn("/api/v1/tracked-instrument-profiles?", self.service)

    def test_summary_renders_only_enabled_initial_profile_labels(self) -> None:
        self.assertIn("profiles.filter((profile) => profile.enabled)", self.summary)
        self.assertIn("earnings: 'Tulosjulkaisut'", self.summary)
        self.assertIn("trend: 'Trendi'", self.summary)
        self.assertIn("future_tech: 'Future Tech'", self.summary)
        self.assertIn("labels.join(' · ')", self.summary)
        self.assertNotIn("catalyst", self.summary)
        self.assertNotIn("anomaly", self.summary)

    def test_scanner_batches_visible_persisted_profile_reads_once(self) -> None:
        self.assertIn("const visibleTrackedInstrumentIds = useMemo", self.scanner)
        self.assertIn("void getTrackingProfilesBatch(visibleTrackedInstrumentIds)", self.scanner)
        self.assertIn("profilesByInstrument", self.scanner)
        self.assertNotIn("getTrackingProfiles(trackedInstrument.id)", self.scanner)
        self.assertIn("<TrackingProfileSummary profiles={profiles} />", self.scanner)

    def test_successful_editor_save_updates_and_reconciles_card_summary(self) -> None:
        self.assertIn("onSaved?: (profile: TrackedInstrumentProfile) => void;", self.editor)
        self.assertIn("onSaved?.(saved);", self.editor)
        self.assertIn("onSaved={applySavedProfile}", self.scanner)
        self.assertIn("profileMutationVersion.current += 1;", self.scanner)
        self.assertIn("setProfileBatchRefreshToken((current) => current + 1);", self.scanner)

    def test_summary_batch_failures_do_not_break_scanner_rows_or_show_stale_key(self) -> None:
        self.assertIn(".catch(() => {", self.scanner)
        self.assertIn("Profile annotations are supplemental", self.scanner)
        self.assertIn("const profileBatchReady = profileBatch.key === visibleProfileBatchKey;", self.scanner)
        self.assertIn("trackedInstrument && profileBatchReady", self.scanner)

    def test_profile_summary_does_not_create_events_or_trading_state(self) -> None:
        source = (self.scanner + self.editor + self.summary + self.service).lower()
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
