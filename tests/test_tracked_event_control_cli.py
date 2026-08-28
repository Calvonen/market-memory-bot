from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/tracked_event_control.py"


class TrackedEventControlCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_calendar_binding_option_is_not_exposed(self) -> None:
        self.assertNotIn('parser.add_argument("--calendar-event-id"', self.source)
        self.assertIn("Calendar-owned events must be promoted through the", self.source)
        self.assertIn("calendar_event_id=None", self.source)


if __name__ == "__main__":
    unittest.main()
