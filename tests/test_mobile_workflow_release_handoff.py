from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "mobile/src/components/TrackedEventsSection.tsx"
HANDOFF = ROOT / "mobile/src/app/tracked-events/[eventId]/release.tsx"


class MobileWorkflowReleaseHandoffTests(unittest.TestCase):
    def test_release_cta_uses_canonical_tracked_event_route_only(self):
        source = COMPONENT.read_text(encoding="utf-8")
        self.assertIn("step.action_target === 'release'", source)
        self.assertIn("step.status === 'action_required'", source)
        self.assertIn("pathname: '/tracked-events/[eventId]/release'", source)
        self.assertIn("params: { eventId }", source)
        self.assertNotIn("pathname: '/events/[eventId]'", source)

    def test_handoff_screen_is_read_only_and_tracked_event_scoped(self):
        source = HANDOFF.read_text(encoding="utf-8")
        self.assertIn("useLocalSearchParams<{ eventId: string }>()", source)
        self.assertIn("read-only", source)
        self.assertNotIn("apiPost", source)
        self.assertNotIn("apiPatch", source)
        self.assertNotIn("apiDelete", source)


if __name__ == "__main__":
    unittest.main()
