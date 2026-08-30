from __future__ import annotations

import re
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


def _tracked_event_value_imports(source: str) -> set[str]:
    match = re.search(
        r"import\s*\{(?P<body>[^}]*)\}\s*from\s*['\"]@/services/tracked-events['\"];",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError("tracked-events service import not found")

    names: set[str] = set()
    for raw_item in match.group("body").split(","):
        item = raw_item.strip()
        if not item or item.startswith("type "):
            continue
        imported_name = item.split(" as ", 1)[0].strip()
        if imported_name:
            names.add(imported_name)
    return names


def _tracked_event_service_calls(screen_source: str, function_source: str) -> set[str]:
    imported = _tracked_event_value_imports(screen_source)
    called = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", function_source))
    return called & imported


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
        guarded_update = _braced_block(success_path, guard)
        self.assertIn("setReleaseSource(currentSource)", guarded_update)
        self.assertIn("setWorkflow(currentWorkflow)", guarded_update)
        self.assertIn("setSourceUrl(currentSource.source_url ?? '')", guarded_update)
        self.assertIn("setSourceTitle(currentSource.source_title ?? '')", guarded_update)

    def test_mobile_ingestion_executes_only_canonical_release_helpers(self) -> None:
        screen = SCREEN_PATH.read_text(encoding="utf-8")
        process_release = _braced_block(screen, "async function processRelease() {")

        self.assertEqual(
            _tracked_event_service_calls(screen, process_release),
            {
                "ingestTrackedEventRelease",
                "getTrackedEventReleaseSource",
                "getTrackedEventWorkflow",
            },
        )


if __name__ == "__main__":
    unittest.main()
