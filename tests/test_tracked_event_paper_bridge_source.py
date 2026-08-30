from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TrackedEventPaperBridgeSourceTests(unittest.TestCase):
    def test_tracked_bridge_never_calls_daily_reaction_helper(self) -> None:
        source = (ROOT / "trading_system" / "tracked_event_paper_bridge.py").read_text()
        self.assertNotIn("_event_price_reaction_pct", source)
        self.assertNotIn("fetch_ohlcv", source)


if __name__ == "__main__":
    unittest.main()
