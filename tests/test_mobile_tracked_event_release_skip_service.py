import unittest
from pathlib import Path


SERVICE_PATH = Path("mobile/src/services/tracked-events.ts")


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("\n}\n", start)
    return source[start:end]


class MobileTrackedEventReleaseSkipServiceTests(unittest.TestCase):
    def test_skip_service_uses_canonical_control_endpoint_with_actor_and_reason(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        body = _function_body(source, "export function skipTrackedEventRelease(")

        self.assertIn("apiControlPost<TrackedEventReleaseSkipResult>", body)
        self.assertIn("/release-skip`", body)
        self.assertIn("{ reason: normalizedReason }", body)
        self.assertIn("{ 'X-MarketAI-Actor': actor }", body)
        self.assertNotIn("release-ingestion", body)
        self.assertNotIn("createTradingTask", body)

    def test_skip_result_is_explicitly_skipped(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        self.assertIn("export type TrackedEventReleaseSkipResult", source)
        self.assertIn("status: 'skipped';", source)


if __name__ == "__main__":
    unittest.main()
