from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "mobile/src/components/TrackedEventsSection.tsx"
HANDOFF = ROOT / "mobile/src/app/tracked-events/[eventId]/release.tsx"


class MobileWorkflowReleaseHandoffTests(unittest.TestCase):
    def test_release_cta_uses_canonical_tracked_event_route_only(self):
        source = COMPONENT.read_text(encoding="utf-8")
        release_condition = "step.status === 'action_required' && step.action_target === 'release'"
        self.assertIn(release_condition, source)

        release_start = source.index(release_condition)
        release_block = source[release_start : release_start + 900]
        self.assertIn("pathname: '/tracked-events/[eventId]/release'", release_block)
        self.assertIn("params: { eventId }", release_block)
        self.assertNotIn("pathname: '/events/[eventId]'", release_block)

    def test_handoff_screen_is_tracked_event_scoped_and_release_source_only(self):
        source = HANDOFF.read_text(encoding="utf-8")
        self.assertIn("useLocalSearchParams<{ eventId: string }>()", source)
        self.assertIn("putTrackedEventReleaseSource", source)
        self.assertNotIn("apiPost", source)
        self.assertNotIn("apiPatch", source)
        self.assertNotIn("apiDelete", source)
        self.assertNotIn("/ingest", source)
        self.assertNotIn("/retry", source)
        self.assertNotIn("trading-task", source)


if __name__ == "__main__":
    unittest.main()
