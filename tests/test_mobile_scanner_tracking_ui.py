import unittest
from pathlib import Path


SCANNER_PATH = Path("mobile/src/app/(tabs)/scanner.tsx")


def _tracking_action(source: str) -> str:
    start = source.index("  async function addScannerResultToTracking(")
    end = source.index("\n  useEffect(() => {", start)
    return source[start:end]


def _tracking_control(source: str) -> str:
    start = source.index("          const status = trackingStatus[")
    end = source.index("        })}", start)
    return source[start:end]


class MobileScannerTrackingUiTests(unittest.TestCase):
    def test_scanner_uses_only_canonical_tracked_instrument_service(self) -> None:
        source = SCANNER_PATH.read_text(encoding="utf-8")
        self.assertIn("getTrackedInstruments", source)
        self.assertIn("trackInstrument", source)
        self.assertIn("from '@/services/tracked-instruments';", source)
        self.assertNotIn("apiControlPost", source)
        self.assertNotIn("apiPost", source)
        self.assertNotIn("fetch(", source)
        self.assertIn("const TRACKING_ACTOR = 'mobile-scanner';", source)

    def test_tracking_state_identity_is_country_and_ticker(self) -> None:
        source = SCANNER_PATH.read_text(encoding="utf-8")
        action = _tracking_action(source)
        control = _tracking_control(source)

        self.assertIn("const trackingKey = `${country}:${row.ticker}`;", action)
        self.assertIn("[trackingKey]: 'saving'", action)
        self.assertIn("[trackingKey]: 'error'", action)
        self.assertNotIn("[trackingKey]: 'saved'", action)
        self.assertIn("const status = trackingStatus[`${country}:${row.ticker}`];", control)
        self.assertNotIn("scope", action)
        self.assertNotIn("scope", control)

    def test_tracking_control_uses_persisted_saved_state_with_local_pending_retry(self) -> None:
        source = SCANNER_PATH.read_text(encoding="utf-8")
        control = _tracking_control(source)

        self.assertIn("matchesTrackedInstrument(item, row.ticker, country)", control)
        self.assertIn("'Lisää seurantaan'", control)
        self.assertIn("'Lisätään…'", control)
        self.assertIn("'Seurannassa'", control)
        self.assertIn("'Yritä uudelleen'", control)
        self.assertIn("disabled={status === 'saving' || isTracked}", control)
        self.assertNotIn("status === 'saved'", control)

    def test_successful_tracking_merges_backend_returned_canonical_row(self) -> None:
        source = SCANNER_PATH.read_text(encoding="utf-8")
        action = _tracking_action(source)

        self.assertIn("const saved = await trackInstrument(", action)
        self.assertIn("current.filter((item) => item.id !== saved.id)", action)
        self.assertIn("saved,", action)
        self.assertIn("delete next[trackingKey]", action)

    def test_scanner_tracking_action_does_not_create_downstream_events(self) -> None:
        action = _tracking_action(SCANNER_PATH.read_text(encoding="utf-8")).lower()
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


if __name__ == "__main__":
    unittest.main()
