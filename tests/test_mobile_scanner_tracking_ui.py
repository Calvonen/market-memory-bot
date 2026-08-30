import unittest
from pathlib import Path


SCANNER_PATH = Path("mobile/src/app/(tabs)/scanner.tsx")


class MobileScannerTrackingUiTests(unittest.TestCase):
    def test_scanner_uses_canonical_tracked_instrument_service(self) -> None:
        source = SCANNER_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "import { trackInstrument } from '@/services/tracked-instruments';",
            source,
        )
        self.assertIn("const TRACKING_ACTOR = 'mobile-scanner';", source)

        start = source.index("  async function addScannerResultToTracking(")
        end = source.index("\n  useEffect(() => {", start)
        action = source[start:end]

        expected_call = """await trackInstrument(
        {
          instrument: row.ticker,
          company_name: '',
          market: country,
          source: 'scanner',
        },
        TRACKING_ACTOR,
      );"""
        self.assertIn(expected_call, action)

    def test_scanner_tracking_action_does_not_create_downstream_events(self) -> None:
        source = SCANNER_PATH.read_text(encoding="utf-8")
        start = source.index("  async function addScannerResultToTracking(")
        end = source.index("\n  useEffect(() => {", start)
        action = source[start:end].lower()

        for forbidden in (
            "tracked-events",
            "calendar/",
            "trading-tasks",
            "strategy",
            "risk",
            "broker",
            "paper",
            "live-execution",
        ):
            self.assertNotIn(forbidden, action)

    def test_scanner_exposes_minimal_tracking_states(self) -> None:
        source = SCANNER_PATH.read_text(encoding="utf-8")
        self.assertIn("'Lisää seurantaan'", source)
        self.assertIn("'Lisätään…'", source)
        self.assertIn("'Seurannassa'", source)
        self.assertIn("'Yritä uudelleen'", source)
        self.assertIn("disabled={status === 'saving' || status === 'saved'}", source)


if __name__ == "__main__":
    unittest.main()
