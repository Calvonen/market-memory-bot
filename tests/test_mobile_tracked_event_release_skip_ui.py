import unittest
from pathlib import Path


SCREEN_PATH = Path("mobile/src/app/tracked-events/[eventId]/release.tsx")


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("\n  }\n", start)
    return source[start:end]


class MobileTrackedEventReleaseSkipUiTests(unittest.TestCase):
    def test_skip_is_gated_by_canonical_release_action_not_active_source(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "releaseStep?.status === 'action_required' && releaseStep.action_target === 'release'",
            source,
        )
        self.assertIn("const canSkipRelease = releaseActionRequired;", source)
        self.assertNotIn("const canSkipRelease = Boolean(releaseSource?.active", source)

    def test_skip_requires_actor_reason_and_calls_only_skip_service(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8")
        body = _function_body(source, "  async function skipRelease() {")
        self.assertIn("const normalizedActor = actor.trim();", body)
        self.assertIn("const normalizedReason = skipReason.trim();", body)
        self.assertIn("skipTrackedEventRelease(", body)
        self.assertIn("normalizedActor", body)
        self.assertIn("normalizedReason", body)
        self.assertNotIn("ingestTrackedEventRelease(", body)
        self.assertNotIn("putTrackedEventReleaseSource(", body)

    def test_skip_refreshes_canonical_source_and_workflow_and_guards_stale_route(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8")
        body = _function_body(source, "  async function skipRelease() {")
        self.assertIn("getTrackedEventReleaseSource(submittedEventId)", body)
        self.assertIn("getTrackedEventWorkflow(submittedEventId)", body)
        self.assertIn("mountedRef.current && eventIdRef.current === submittedEventId", body)
        self.assertIn("setWorkflow(currentWorkflow)", body)
        self.assertIn("setSkipReason('')", body)

    def test_skip_button_requires_reason_and_serializes_with_other_writes(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8")
        self.assertIn("!canSkipRelease || !actor.trim() || !skipReason.trim() || skipping || processing || submitting", source)
        self.assertIn("if (!eventId || !canSkipRelease || skipping || processing || submitting) return;", source)
        self.assertIn("submitting || processing || skipping", source)
        self.assertIn("processing || submitting || skipping", source)

    def test_ui_explains_audited_skip_boundary(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8")
        self.assertIn("Ohita julkaisu", source)
        self.assertIn("Ohitus on auditoitu päätös.", source)
        self.assertIn("ei luo kaupankäyntitehtävää", source)
        self.assertIn("Strategy/Risk/Broker", source)


if __name__ == "__main__":
    unittest.main()
