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

    def test_summary_is_display_only_and_profile_service_keeps_canonical_batch_read(self) -> None:
        self.assertNotIn("getTrackingProfiles", self.summary)
        self.assertNotIn("useEffect", self.summary)
        self.assertNotIn("fetch(", self.summary)
        self.assertNotIn("setTrackingProfile", self.summary)
        self.assertNotIn("getTrackingProfilesBatch", self.scanner)
        self.assertIn("/api/v1/tracked-instrument-profiles?", self.service)

    def test_summary_renders_only_enabled_initial_profile_labels(self) -> None:
        self.assertIn("profiles.filter((profile) => profile.enabled)", self.summary)
        self.assertIn("earnings: 'Tulosjulkaisut'", self.summary)
        self.assertIn("trend: 'Trendi'", self.summary)
        self.assertIn("future_tech: 'Future Tech'", self.summary)
        self.assertIn("labels.join(' · ')", self.summary)
        self.assertNotIn("catalyst", self.summary)
        self.assertNotIn("anomaly", self.summary)

    def test_scanner_does_not_batch_profile_reads_or_render_summary(self) -> None:
        self.assertNotIn("getTrackingProfilesBatch", self.scanner)
        self.assertNotIn("visibleTrackedInstrumentIds", self.scanner)
        self.assertNotIn("profilesByInstrument", self.scanner)
        self.assertNotIn("TrackingProfileSummary", self.scanner)
        self.assertNotIn("@/components/tracking-profile-summary", self.scanner)

    def test_editor_save_callback_remains_component_contract_not_scanner_wiring(self) -> None:
        self.assertIn("onSaved?: (profile: TrackedInstrumentProfile) => void;", self.editor)
        self.assertIn("onSaved?.(saved);", self.editor)
        self.assertNotIn("onSaved={applySavedProfile}", self.scanner)
        self.assertNotIn("profileMutationVersion", self.scanner)
        self.assertNotIn("profileBatchRefreshToken", self.scanner)

    def test_scanner_has_no_profile_batch_error_or_stale_state_contract(self) -> None:
        self.assertNotIn("Profile annotations are supplemental", self.scanner)
        self.assertNotIn("profileBatchReady", self.scanner)
        self.assertNotIn("visibleProfileBatchKey", self.scanner)
        self.assertNotIn("profileBatch", self.scanner)

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
