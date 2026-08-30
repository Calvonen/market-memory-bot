import unittest
from pathlib import Path


SCANNER_PATH = Path("mobile/src/app/(tabs)/scanner.tsx")


EXPECTED_TRACKING_ACTION = """  async function addScannerResultToTracking(row: ScannerResult['results'][number]) {
    const trackingKey = `${country}:${row.ticker}`;
    setTrackingStatus((current) => ({ ...current, [trackingKey]: 'saving' }));

    try {
      await trackInstrument(
        {
          instrument: row.ticker,
          company_name: '',
          market: country,
          source: 'scanner',
        },
        TRACKING_ACTOR,
      );
      setTrackingStatus((current) => ({ ...current, [trackingKey]: 'saved' }));
    } catch {
      setTrackingStatus((current) => ({ ...current, [trackingKey]: 'error' }));
    }
  }
"""


EXPECTED_TRACKING_CONTROL = """          const status = trackingStatus[`${country}:${row.ticker}`];
          return (
            <View key={row.ticker} style={shared.card}>
              <Text style={shared.heading}>
                {row.ticker} · {row.price.toFixed(2)}
              </Text>
              <Text style={shared.text}>
                {row.direction} · samankaltaisuus {row.best_similarity ?? '–'}
              </Text>
              <Pressable
                accessibilityRole=\"button\"
                disabled={status === 'saving' || status === 'saved'}
                onPress={() => void addScannerResultToTracking(row)}
                style={shared.button}>
                <Text style={shared.buttonText}>
                  {status === 'saving'
                    ? 'Lisätään…'
                    : status === 'saved'
                      ? 'Seurannassa'
                      : status === 'error'
                        ? 'Yritä uudelleen'
                        : 'Lisää seurantaan'}
                </Text>
              </Pressable>
            </View>
          );
"""


def _tracking_action(source: str) -> str:
    start = source.index("  async function addScannerResultToTracking(")
    end = source.index("\n  useEffect(() => {", start)
    return source[start:end] + "\n"


def _tracking_control(source: str) -> str:
    start = source.index("          const status = trackingStatus[")
    end = source.index("        })}", start)
    return source[start:end]


class MobileScannerTrackingUiTests(unittest.TestCase):
    def test_scanner_uses_only_canonical_tracked_instrument_mutation(self) -> None:
        source = SCANNER_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "import { trackInstrument } from '@/services/tracked-instruments';",
            source,
        )
        self.assertNotIn("apiControlPost", source)
        self.assertNotIn("apiPost", source)
        self.assertNotIn("fetch(", source)
        self.assertIn("const TRACKING_ACTOR = 'mobile-scanner';", source)
        self.assertEqual(_tracking_action(source), EXPECTED_TRACKING_ACTION)

    def test_tracking_state_identity_is_country_and_ticker(self) -> None:
        source = SCANNER_PATH.read_text(encoding="utf-8")
        action = _tracking_action(source)
        control = _tracking_control(source)

        self.assertIn("const trackingKey = `${country}:${row.ticker}`;", action)
        self.assertIn("[trackingKey]: 'saving'", action)
        self.assertIn("[trackingKey]: 'saved'", action)
        self.assertIn("[trackingKey]: 'error'", action)
        self.assertIn("const status = trackingStatus[`${country}:${row.ticker}`];", control)
        self.assertNotIn("scope", action)
        self.assertNotIn("scope", control)

    def test_tracking_control_has_minimal_saving_saved_retry_states(self) -> None:
        source = SCANNER_PATH.read_text(encoding="utf-8")
        control = _tracking_control(source)

        self.assertEqual(control, EXPECTED_TRACKING_CONTROL)
        self.assertIn("'Lisää seurantaan'", control)
        self.assertIn("'Lisätään…'", control)
        self.assertIn("'Seurannassa'", control)
        self.assertIn("'Yritä uudelleen'", control)
        self.assertIn("disabled={status === 'saving' || status === 'saved'}", control)

    def test_scanner_tracking_action_does_not_create_downstream_events(self) -> None:
        action = EXPECTED_TRACKING_ACTION.lower()
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
