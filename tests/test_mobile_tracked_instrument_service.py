import unittest
from pathlib import Path


SERVICE_PATH = Path("mobile/src/services/tracked-instruments.ts")


class MobileTrackedInstrumentServiceTests(unittest.TestCase):
    def test_service_uses_canonical_control_endpoint_and_actor_header(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        start = source.index("export function trackInstrument(")
        body = source[start:]

        self.assertIn("apiControlPost<TrackedInstrument>", body)
        self.assertIn("'/api/v1/tracked-instruments'", body)
        self.assertIn("{ 'X-MarketAI-Actor': normalizedActor }", body)
        self.assertNotIn("tracked-events", body)
        self.assertNotIn("calendar/", body)
        self.assertNotIn("strategy", body.lower())
        self.assertNotIn("risk", body.lower())
        self.assertNotIn("broker", body.lower())
        self.assertNotIn("paper", body.lower())

    def test_service_normalizes_only_instrument_metadata_and_keeps_source_explicit(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        start = source.index("export function trackInstrument(")
        body = source[start:]

        self.assertIn("const normalizedActor = actor.trim();", body)
        self.assertIn("const normalizedInstrument = input.instrument.trim();", body)
        self.assertIn("company_name: input.company_name?.trim() ?? ''", body)
        self.assertIn("market: input.market?.trim() ?? ''", body)
        self.assertIn("source: input.source", body)
        self.assertNotIn("actor: normalizedActor", body)

    def test_service_rejects_invalid_actor_and_blank_instrument_before_post(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        start = source.index("export function trackInstrument(")
        body = source[start:]

        actor_guard = body.index("if (!normalizedActor || normalizedActor.length > 200)")
        instrument_guard = body.index("if (!normalizedInstrument)")
        post = body.index("apiControlPost<TrackedInstrument>")
        self.assertLess(actor_guard, post)
        self.assertLess(instrument_guard, post)


if __name__ == "__main__":
    unittest.main()
