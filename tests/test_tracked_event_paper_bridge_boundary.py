from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TrackedEventPaperBridgeBoundaryTests(unittest.TestCase):
    def test_bridge_has_no_persistence_or_broker_writes(self) -> None:
        source = (ROOT / "trading_system" / "tracked_event_paper_bridge.py").read_text()
        forbidden = (
            ".rpc(",
            ".table(",
            "save_result(",
            "save_reaction(",
            "mark_monitoring(",
            "mark_completed(",
            "PaperBroker(",
            "EtoroDemoBroker(",
        )
        for marker in forbidden:
            self.assertNotIn(marker, source)

    def test_bridge_delegates_to_existing_post_release_pipeline(self) -> None:
        source = (ROOT / "trading_system" / "tracked_event_paper_bridge.py").read_text()
        self.assertIn("run_post_release_paper(", source)
        self.assertIn("confirmed_reaction_pct=float(confirmation.return_pct)", source)


if __name__ == "__main__":
    unittest.main()
