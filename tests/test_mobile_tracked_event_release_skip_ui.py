import re
import unittest
from collections import Counter
from pathlib import Path


SCREEN_PATH = Path("mobile/src/app/tracked-events/[eventId]/release.tsx")


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("\n  }\n", start)
    return source[start:end]


def _tracked_event_service_names(source: str) -> list[str]:
    match = re.search(
        r"import\s*\{(?P<body>[^}]*)\}\s*from\s*['\"]@/services/tracked-events['\"];",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError("tracked-events import block not found")
    names: list[str] = []
    for item in match.group("body").split(","):
        normalized = item.strip()
        if not normalized or normalized.startswith("type "):
            continue
        names.append(normalized.split(" as ", 1)[-1].strip())
    return names


def _service_call_multiset(source: str, body: str) -> Counter[str]:
    imported = _tracked_event_service_names(source)
    calls: Counter[str] = Counter()
    for name in imported:
        calls[name] = len(re.findall(rf"\b{re.escape(name)}\s*\(", body))
    return +calls


def _service_modules(source: str) -> set[str]:
    return set(re.findall(r"from\s*['\"](?P<module>@/services/[^'\"]+)['\"]", source))


def _assert_refresh_setters_are_route_guarded(test: unittest.TestCase, branch: str) -> None:
    guarded_refresh = re.search(
        r"if\s*\(mountedRef\.current\s*&&\s*eventIdRef\.current\s*===\s*submittedEventId\)\s*\{"
        r"(?P<body>.*?)"
        r"\n\s*\}",
        branch,
        flags=re.DOTALL,
    )
    test.assertIsNotNone(guarded_refresh)
    guard_body = guarded_refresh.group("body") if guarded_refresh else ""
    test.assertIn("setReleaseSource(currentSource)", guard_body)
    test.assertIn("setWorkflow(currentWorkflow)", guard_body)


class MobileTrackedEventReleaseSkipUiTests(unittest.TestCase):
    def test_skip_is_gated_by_canonical_release_action_not_active_source(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "releaseStep?.status === 'action_required' && releaseStep.action_target === 'release'",
            source,
        )
        self.assertIn("const canSkipRelease = releaseActionRequired;", source)
        self.assertNotIn("const canSkipRelease = Boolean(releaseSource?.active", source)

    def test_skip_requires_actor_reason_and_uses_exact_canonical_service_calls(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8")
        body = _function_body(source, "  async function skipRelease() {")
        self.assertIn("const normalizedActor = actor.trim();", body)
        self.assertIn("const normalizedReason = skipReason.trim();", body)
        self.assertEqual(
            _service_call_multiset(source, body),
            Counter(
                {
                    "skipTrackedEventRelease": 1,
                    "getTrackedEventReleaseSource": 2,
                    "getTrackedEventWorkflow": 2,
                }
            ),
        )
        self.assertEqual(_service_modules(source), {"@/services/tracked-events"})
        for direct_request in (
            "fetch(",
            "apiGet(",
            "apiPost(",
            "apiControlPost(",
            "apiPut(",
            "apiControlPut(",
            "apiDelete(",
            "apiControlDelete(",
        ):
            self.assertNotIn(direct_request, body)
        self.assertIn("normalizedActor", body)
        self.assertIn("normalizedReason", body)

    def test_skip_refreshes_and_guards_success_and_failure_independently(self) -> None:
        source = SCREEN_PATH.read_text(encoding="utf-8")
        body = _function_body(source, "  async function skipRelease() {")
        try_index = body.index("    try {")
        catch_marker = "\n    } catch (skipError) {"
        catch_index = body.index(catch_marker, try_index)
        finally_index = body.index("\n    } finally {", catch_index)
        success = body[try_index:catch_index]
        failure = body[catch_index:finally_index]

        for branch in (success, failure):
            self.assertEqual(branch.count("getTrackedEventReleaseSource(submittedEventId)"), 1)
            self.assertEqual(branch.count("getTrackedEventWorkflow(submittedEventId)"), 1)
            _assert_refresh_setters_are_route_guarded(self, branch)

        self.assertIn("setSkipReason('')", success)
        self.assertNotIn("setSkipReason('')", failure)

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
