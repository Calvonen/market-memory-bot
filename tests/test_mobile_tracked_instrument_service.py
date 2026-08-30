import unittest
from pathlib import Path


SERVICE_PATH = Path("mobile/src/services/tracked-instruments.ts")


EXPECTED_TRACK_INSTRUMENT_IMPLEMENTATION = """export function trackInstrument(
  input: TrackInstrumentInput,
  actor: string,
  post: TrackedInstrumentPost = apiControlPost,
): Promise<TrackedInstrument> {
  const normalizedActor = actor.trim();
  if (!normalizedActor || normalizedActor.length > 200) {
    return Promise.reject(
      new Error('Tracking actor must be nonblank and at most 200 characters'),
    );
  }

  const normalizedInstrument = input.instrument.trim();
  if (!normalizedInstrument) {
    return Promise.reject(new Error('Instrument must be nonblank'));
  }

  return post<TrackedInstrument>(
    '/api/v1/tracked-instruments',
    {
      instrument: normalizedInstrument,
      company_name: input.company_name?.trim() ?? '',
      market: input.market?.trim() ?? '',
      source: input.source,
    },
    { 'X-MarketAI-Actor': normalizedActor },
  );
}
"""


def _tracked_instrument_implementation(source: str) -> str:
    start = source.index("export function trackInstrument(")
    end = start + len(EXPECTED_TRACK_INSTRUMENT_IMPLEMENTATION)
    return source[start:end]


class MobileTrackedInstrumentServiceTests(unittest.TestCase):
    def test_service_keeps_exact_canonical_implementation_boundary(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        implementation = _tracked_instrument_implementation(source)

        self.assertEqual(implementation, EXPECTED_TRACK_INSTRUMENT_IMPLEMENTATION)

    def test_unrelated_declaration_after_service_does_not_expand_snapshot(self) -> None:
        source = EXPECTED_TRACK_INSTRUMENT_IMPLEMENTATION + "\nexport const unrelated = true;\n"
        self.assertEqual(
            _tracked_instrument_implementation(source),
            EXPECTED_TRACK_INSTRUMENT_IMPLEMENTATION,
        )

    def test_service_keeps_exact_source_union_and_input_contract(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "export type TrackedInstrumentSource = 'scanner' | 'calendar' | 'manual';",
            source,
        )

        input_start = source.index("export type TrackInstrumentInput = {")
        input_end = source.index("\n};", input_start) + len("\n};")
        input_block = source[input_start:input_end]
        self.assertEqual(
            input_block,
            """export type TrackInstrumentInput = {
  instrument: string;
  company_name?: string;
  market?: string;
  source: TrackedInstrumentSource;
};""",
        )

    def test_service_has_no_downstream_tracking_or_trading_paths(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        implementation = _tracked_instrument_implementation(source)

        forbidden = (
            "tracked-events",
            "calendar/",
            "trading-tasks",
            "strategy",
            "risk",
            "broker",
            "paper",
            "live-execution",
        )
        lowered = implementation.lower()
        for value in forbidden:
            self.assertNotIn(value, lowered)


if __name__ == "__main__":
    unittest.main()
