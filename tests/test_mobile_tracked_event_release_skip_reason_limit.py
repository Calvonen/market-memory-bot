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
            "Array.from(normalizedReason).length > TRACKED_EVENT_RELEASE_SKIP_REASON_MAX_LENGTH",
            body,
        )

        blank_index = body.index("if (!normalizedReason)")
        limit_index = body.index(
            "Array.from(normalizedReason).length > TRACKED_EVENT_RELEASE_SKIP_REASON_MAX_LENGTH"
        )
        post_index = body.index("apiControlPost<TrackedEventReleaseSkipResult>")
        self.assertLess(blank_index, post_index)
        self.assertLess(limit_index, post_index)
        self.assertIn("{ reason: normalizedReason }", body)

    def test_reason_length_uses_unicode_code_points_for_backend_parity(self) -> None:
        source = SERVICE_PATH.read_text(encoding="utf-8")
        start = source.index("export function skipTrackedEventRelease(")
        body = source[start:]

        self.assertIn("Array.from(normalizedReason).length", body)
        self.assertNotIn("normalizedReason.length > TRACKED_EVENT_RELEASE_SKIP_REASON_MAX_LENGTH", body)


if __name__ == "__main__":
    unittest.main()
