from __future__ import annotations

import unittest
from pathlib import Path


API_PATH = Path("mobile/src/services/api.ts")
SERVICE_PATH = Path("mobile/src/services/tracked-events.ts")
SCREEN_PATH = Path("mobile/src/app/tracked-events/[eventId]/release.tsx")


def _braced_block(source: str, signature: str) -> str:
    start = source.index(signature)
    brace_start = source.index("{", start)
    depth = 0
    for index in range(brace_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Unterminated block for {signature}")


class MobileTrackedEventReleaseIngestionTests(unittest.TestCase):
    def test_service_uses_control_authenticated_canonical_ingestion_route(self) -> None:
        service_source = SERVICE_PATH.read_text(encoding="utf-8")
        ingestion = _braced_block(service_source, "export function ingestTrackedEventRelease(")

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
        process_release = _braced_block(screen, "async function processRelease() {")

        self.assertIn(expected_gate, screen)
        self.assertIn(
            "if (!eventId || !canProcessRelease || processing || submitting) return;",
            process_release,
        )
        self.assertIn("disabled={!canProcessRelease || !actor.trim() || processing || submitting}", screen)
        self.assertIn("Käsittele julkaisu", screen)

    def test_ingestion_success_refresh_is_guarded_and_updates_canonical_state(self) -> None:
        screen = SCREEN_PATH.read_text(encoding="utf-8")
        process_release = _braced_block(screen, "async function processRelease() {")

        call = "await ingestTrackedEventRelease(submittedEventId, normalizedActor)"
        self.assertIn(call, process_release)
        success_path = process_release.split(call, 1)[1].split("} catch (processError)", 1)[0]
        self.assertIn("getTrackedEventReleaseSource(submittedEventId)", success_path)
        self.assertIn("getTrackedEventWorkflow(submittedEventId)", success_path)

        guard = "if (mountedRef.current && eventIdRef.current === submittedEventId) {"
        self.assertIn(guard, success_path)
        guarded_update = success_path.split(guard, 1)[1]
        self.assertIn("setReleaseSource(currentSource)", guarded_update)
        self.assertIn("setWorkflow(currentWorkflow)", guarded_update)
        self.assertIn("setSourceUrl(currentSource.source_url ?? '')", guarded_update)
        self.assertIn("setSourceTitle(currentSource.source_title ?? '')", guarded_update)

    def test_mobile_ingestion_executes_only_canonical_release_helpers(self) -> None:
        service_source = SERVICE_PATH.read_text(encoding="utf-8")
        screen = SCREEN_PATH.read_text(encoding="utf-8")
        ingestion_service = _braced_block(service_source, "export function ingestTrackedEventRelease(")
        process_release = _braced_block(screen, "async function processRelease() {")
        executable = ingestion_service + process_release

        self.assertIn("ingestTrackedEventRelease(submittedEventId, normalizedActor)", process_release)
        self.assertIn("getTrackedEventReleaseSource(submittedEventId)", process_release)
        self.assertIn("getTrackedEventWorkflow(submittedEventId)", process_release)
        for forbidden in (
            "skipTrackedEventRelease(",
            "runPostReleasePaper(",
            "runStrategy(",
            "runRisk(",
            "broker.",
            "/release-skip",
            "paper-run",
            "trading-task",
            "run_post_release_paper",
        ):
            self.assertNotIn(forbidden.lower(), executable.lower())


if __name__ == "__main__":
    unittest.main()
