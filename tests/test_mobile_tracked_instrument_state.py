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

    def test_scanner_loads_persisted_tracking_state_independently_from_scan_results(self) -> None:
        self.assertIn("getTrackedInstruments", self.scanner)
        self.assertNotIn("Promise.all", self.scanner)
        self.assertIn("void getTrackedInstruments()", self.scanner)
        self.assertIn("apiGet<ScannerResult>(", self.scanner)
        self.assertIn("setTrackedInstruments(canonicalTrackedInstruments)", self.scanner)
        self.assertIn("matchesTrackedInstrument(item, row.ticker, country)", self.scanner)

    def test_tracked_state_read_failure_does_not_fail_scanner_load(self) -> None:
        tracked_read_start = self.scanner.index("void getTrackedInstruments()")
        scanner_try_start = self.scanner.index("    try {", tracked_read_start)
        tracked_read_block = self.scanner[tracked_read_start:scanner_try_start]
        self.assertIn(".catch(() => {", tracked_read_block)
        self.assertNotIn("setError(", tracked_read_block)

    def test_stale_tracked_reads_cannot_replace_newer_mutation_state(self) -> None:
        self.assertIn("const trackedMutationVersion = useRef(0);", self.scanner)
        self.assertIn("const trackedVersionAtStart = trackedMutationVersion.current;", self.scanner)
        self.assertIn(
            "trackedVersionAtStart === trackedMutationVersion.current",
            self.scanner,
        )
        action_start = self.scanner.index("async function addScannerResultToTracking(")
        action_end = self.scanner.index("\n  useEffect(() => {", action_start)
        action = self.scanner[action_start:action_end]
        self.assertGreaterEqual(action.count("trackedMutationVersion.current += 1;"), 2)
        success_increment = action.index("trackedMutationVersion.current += 1;", action.index("const saved = await trackInstrument("))
        saved_state_update = action.index("setTrackedInstruments((current) => [")
        self.assertLess(success_increment, saved_state_update)

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
