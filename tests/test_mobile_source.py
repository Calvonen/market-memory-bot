import unittest
from pathlib import Path


HOME_SCREEN = Path("mobile/src/app/index.tsx")
NATIVE_TABS = Path("mobile/src/components/app-tabs.tsx")


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

    def test_unified_navigation_preserves_event_dashboard(self) -> None:
        tabs = NATIVE_TABS.read_text(encoding="utf-8")
        for route in ('name="index"', 'name="memory"', 'name="scanner"', 'name="trades"', 'name="settings"'):
            self.assertIn(route, tabs)
        self.assertIn("paper-status", self.source)

    def test_market_memory_screen_has_mobile_states(self) -> None:
        source = Path("mobile/src/app/memory.tsx").read_text(encoding="utf-8")
        for text in ("Ticker", "Analysoi", "ActivityIndicator", "Tärkeimmät analogiat"):
            self.assertIn(text, source)

    def test_new_market_memory_request_hides_previous_result(self) -> None:
        source = Path("mobile/src/app/memory.tsx").read_text(encoding="utf-8")
        request_start = source.index("async function analyze(")
        clear_data = source.index("setData(null)", request_start)
        start_loading = source.index("setLoading(true)", request_start)
        request = source.index("await apiGet", request_start)

        # A successful A result is removed before B enters its loading state.
        self.assertLess(clear_data, start_loading)
        self.assertLess(start_loading, request)
        # A cannot render while B is loading or alongside B's error; a
        # successful B still renders after both transient states clear.
        self.assertIn("!loading && !error && data &&", source)

    def test_ota_configuration_is_channel_bound(self) -> None:
        app = Path("mobile/app.json").read_text(encoding="utf-8")
        eas = Path("mobile/eas.json").read_text(encoding="utf-8")
        package = Path("mobile/package.json").read_text(encoding="utf-8")
        self.assertIn('"expo-updates"', package)
        self.assertIn('"runtimeVersion"', app)
        self.assertIn('"updates"', app)
        self.assertIn('"channel": "preview"', eas)
        self.assertIn('"channel": "production"', eas)


if __name__ == "__main__":
    unittest.main()
