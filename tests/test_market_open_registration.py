from __future__ import annotations

import unittest
from datetime import UTC, datetime

from scripts.register_market_open_event import _next_grounded_open


class MarketOpenRegistrationTests(unittest.TestCase):
    def test_sydney_next_open_comes_from_xasx_calendar(self) -> None:
        session_date, open_at = _next_grounded_open(
            etoro_market="Sydney",
            now=datetime(2026, 9, 2, 18, 0, tzinfo=UTC),
        )

        self.assertEqual(session_date.isoformat(), "2026-09-03")
        self.assertEqual(open_at, datetime(2026, 9, 3, 0, 0, tzinfo=UTC))

    def test_unknown_etoro_market_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported eToro market"):
            _next_grounded_open(
                etoro_market="Unknown Exchange",
                now=datetime(2026, 9, 2, 18, 0, tzinfo=UTC),
            )


if __name__ == "__main__":
    unittest.main()
