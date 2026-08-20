import unittest
from pathlib import Path


HOME_SCREEN = Path("mobile/src/app/index.tsx")


class MobileSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = HOME_SCREEN.read_text(encoding="utf-8")

    def test_rejected_risk_reasons_remain_visible_with_quantity_and_reward_risk(self) -> None:
        self.assertIn("risk.reasons.join(' • ')", self.source)
        self.assertIn("risk?.status === 'REJECT'", self.source)
        self.assertIn("Enimmäismäärä ${risk.max_quantity", self.source)
        self.assertIn("Tuotto/riski ${risk.reward_risk", self.source)

    def test_waiting_confirmation_preserves_the_persisted_reason(self) -> None:
        self.assertIn(
            "run?.status === 'waiting_confirmation' ? run.message : null",
            self.source,
        )
        self.assertIn("{confirmationReason}", self.source)


if __name__ == "__main__":
    unittest.main()
