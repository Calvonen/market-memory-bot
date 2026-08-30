import unittest
from pathlib import Path


SERVICE_PATH = Path("mobile/src/services/tracked-events.ts")


class MobileTrackedEventReleaseSkipReasonLimitTests(unittest.TestCase):
    def test_skip_service_enforces_backend_reason_limit_before_post(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        start = source.index("export function skipTrackedEventRelease(")
        body = source[start:]

        self.assertIn("TRACKED_EVENT_RELEASE_SKIP_REASON_MAX_LENGTH = 1000", source)
        self.assertIn("const normalizedReason = reason.trim();", body)
        self.assertIn("if (!normalizedReason)", body)
        self.assertIn(
            "normalizedReason.length > TRACKED_EVENT_RELEASE_SKIP_REASON_MAX_LENGTH",
            body,
        )
        self.assertLess(
            body.index("normalizedReason.length > TRACKED_EVENT_RELEASE_SKIP_REASON_MAX_LENGTH"),
            body.index("apiControlPost<TrackedEventReleaseSkipResult>"),
        )
        self.assertIn("{ reason: normalizedReason }", body)


if __name__ == "__main__":
    unittest.main()
