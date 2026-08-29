from __future__ import annotations

import unittest
from pathlib import Path


API_PATH = Path("mobile/src/services/api.ts")
SERVICE_PATH = Path("mobile/src/services/tracked-events.ts")
SCREEN_PATH = Path("mobile/src/app/tracked-events/[eventId]/release.tsx")


def _function_block(source: str, signature: str, next_signature: str | None = None) -> str:
    start = source.index(signature)
    if next_signature is None:
        return source[start:]
    end = source.index(next_signature, start)
    return source[start:end]


class MobileTrackedEventReleaseIngestionTests(unittest.TestCase):
    def test_service_uses_control_authenticated_canonical_ingestion_route(self) -> None:
        service_source = SERVICE_PATH.read_text(encoding="utf-8")
        ingestion = _function_block(service_source, "export function ingestTrackedEventRelease(")

        self.assertIn("apiControlPost<TrackedEventReleaseIngestionResult>", ingestion)
        self.assertIn("/release-ingestion", ingestion)
        self.assertIn("{ 'X-MarketAI-Actor': actor }", ingestion)
        self.assertNotIn("X-Admin-Token", ingestion)

    def test_screen_gates_ingestion_on_complete_canonical_release_condition(self) -> None:
        screen = SCREEN_PATH.read_text(encoding="utf-8")
        expected_gate = """const canProcessRelease = Boolean(
    releaseSource?.active
      && releaseStep?.status === 'action_required'
      && releaseStep.action_target === 'release',
  );"""

        self.assertIn(expected_gate, screen)
        self.assertIn("disabled={!canProcessRelease || !actor.trim() || processing || submitting}", screen)
        self.assertIn("Käsittele julkaisu", screen)

    def test_ingestion_refresh_is_guarded_and_updates_canonical_state(self) -> None:
        screen = SCREEN_PATH.read_text(encoding="utf-8")
        process_release = _function_block(screen, "async function processRelease() {", "\n\n  return (")

        call = "await ingestTrackedEventRelease(submittedEventId, normalizedActor)"
        self.assertIn(call, process_release)
        after_call = process_release.split(call, 1)[1]
        self.assertIn("getTrackedEventReleaseSource(submittedEventId)", after_call)
        self.assertIn("getTrackedEventWorkflow(submittedEventId)", after_call)
        guarded_update = """if (mountedRef.current && eventIdRef.current === submittedEventId) {
        setReleaseSource(currentSource);
        setWorkflow(currentWorkflow);"""
        self.assertIn(guarded_update, after_call)
        self.assertIn("setSourceUrl(currentSource.source_url ?? '')", after_call)
        self.assertIn("setSourceTitle(currentSource.source_title ?? '')", after_call)

    def test_mobile_ingestion_does_not_add_skip_or_executable_trading_paths(self) -> None:
        service_source = SERVICE_PATH.read_text(encoding="utf-8")
        screen = SCREEN_PATH.read_text(encoding="utf-8")
        ingestion_service = _function_block(service_source, "export function ingestTrackedEventRelease(")
        process_release = _function_block(screen, "async function processRelease() {", "\n\n  return (")
        executable = ingestion_service + process_release

        self.assertNotIn("/release-skip", executable)
        self.assertNotIn("paper-run", executable)
        self.assertNotIn("trading-task", executable)
        self.assertNotIn("run_post_release_paper", executable)
        self.assertNotIn("broker.", executable.lower())


if __name__ == "__main__":
    unittest.main()
