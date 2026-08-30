import unittest
from pathlib import Path


SERVICE = Path("mobile/src/services/tracked-instruments.ts")
SCANNER = Path("mobile/src/app/(tabs)/scanner.tsx")


class MobileTrackedInstrumentStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = SERVICE.read_text(encoding="utf-8")
        cls.scanner = SCANNER.read_text(encoding="utf-8")

    def test_mobile_service_reads_canonical_tracked_instruments(self) -> None:
        self.assertIn("apiControlPost, apiGet", self.service)
        self.assertIn("export function getTrackedInstruments(", self.service)
        self.assertIn("get<TrackedInstrument[]>('/api/v1/tracked-instruments')", self.service)

    def test_scanner_loads_persisted_tracking_state_with_scan_results(self) -> None:
        self.assertIn("getTrackedInstruments", self.scanner)
        self.assertIn("const [result, canonicalTrackedInstruments] = await Promise.all([", self.scanner)
        self.assertIn("setTrackedInstruments(canonicalTrackedInstruments)", self.scanner)
        self.assertIn("matchesTrackedInstrument(item, row.ticker, country)", self.scanner)

    def test_scanner_does_not_use_session_saved_state_as_canonical_state(self) -> None:
        self.assertNotIn("type TrackingStatus = 'saving' | 'saved' | 'error'", self.scanner)
        self.assertNotIn("[trackingKey]: 'saved'", self.scanner)
        self.assertIn("disabled={status === 'saving' || isTracked}", self.scanner)
        self.assertIn("isTracked\n                      ? 'Seurannassa'", self.scanner)

    def test_successful_track_updates_canonical_instrument_collection(self) -> None:
        self.assertIn("const saved = await trackInstrument(", self.scanner)
        self.assertIn("current.filter((item) => item.id !== saved.id)", self.scanner)
        self.assertIn("saved,", self.scanner)

    def test_tracking_state_remains_instrument_only(self) -> None:
        forbidden = (
            "tracked-events",
            "Strategy",
            "Risk",
            "Broker",
            "paper_order",
            "trading-task",
        )
        service_and_scanner = self.service + self.scanner
        for marker in forbidden:
            self.assertNotIn(marker, service_and_scanner)


if __name__ == "__main__":
    unittest.main()
