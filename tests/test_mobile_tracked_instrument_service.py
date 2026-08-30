import unittest
from pathlib import Path


SERVICE_PATH = Path("mobile/src/services/tracked-instruments.ts")


class MobileTrackedInstrumentServiceTests(unittest.TestCase):
    def test_service_uses_exactly_one_canonical_control_transport_call(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        start = source.index("export function trackInstrument(")
        body = source[start:]

        self.assertEqual(body.count("post<TrackedInstrument>("), 1)
        self.assertNotIn("apiControlPost<TrackedInstrument>(", body)
        self.assertNotIn("fetch(", body)
        self.assertIn("post: TrackedInstrumentPost = apiControlPost", body)

        expected_call = """return post<TrackedInstrument>(
    '/api/v1/tracked-instruments',
    {
      instrument: normalizedInstrument,
      company_name: input.company_name?.trim() ?? '',
      market: input.market?.trim() ?? '',
      source: input.source,
    },
    { 'X-MarketAI-Actor': normalizedActor },
  );"""
        self.assertIn(expected_call, body)

    def test_service_keeps_exact_source_union_and_actor_out_of_payload(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "export type TrackedInstrumentSource = 'scanner' | 'calendar' | 'manual';",
            source,
        )
        self.assertNotIn("export type TrackedInstrumentSource = string", source)

        start = source.index("export function trackInstrument(")
        body = source[start:]
        call_start = body.index("return post<TrackedInstrument>(")
        call_end = body.index("  );", call_start) + len("  );")
        canonical_call = body[call_start:call_end]

        self.assertIn("source: input.source,", canonical_call)
        self.assertNotIn("source: input.source.trim", canonical_call)
        self.assertNotIn("actor:", canonical_call)
        self.assertNotIn("created_by", canonical_call)
        self.assertNotIn("updated_by", canonical_call)
        self.assertNotIn("...input", canonical_call)
        self.assertIn("{ 'X-MarketAI-Actor': normalizedActor }", canonical_call)

    def test_service_normalizes_only_instrument_metadata(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        start = source.index("export function trackInstrument(")
        body = source[start:]

        self.assertIn("const normalizedActor = actor.trim();", body)
        self.assertIn("const normalizedInstrument = input.instrument.trim();", body)
        self.assertIn("company_name: input.company_name?.trim() ?? ''", body)
        self.assertIn("market: input.market?.trim() ?? ''", body)

    def test_invalid_actor_guard_returns_before_transport(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        start = source.index("export function trackInstrument(")
        body = source[start:]

        guard = """if (!normalizedActor || normalizedActor.length > 200) {
    return Promise.reject(
      new Error('Tracking actor must be nonblank and at most 200 characters'),
    );
  }"""
        self.assertIn(guard, body)
        self.assertLess(body.index(guard), body.index("return post<TrackedInstrument>("))

    def test_blank_instrument_guard_returns_before_transport(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        start = source.index("export function trackInstrument(")
        body = source[start:]

        guard = """if (!normalizedInstrument) {
    return Promise.reject(new Error('Instrument must be nonblank'));
  }"""
        self.assertIn(guard, body)
        self.assertLess(body.index(guard), body.index("return post<TrackedInstrument>("))

    def test_service_has_no_downstream_tracking_or_trading_paths(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        start = source.index("export function trackInstrument(")
        body = source[start:]

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
        lowered = body.lower()
        for value in forbidden:
            self.assertNotIn(value, lowered)


if __name__ == "__main__":
    unittest.main()
