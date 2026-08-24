from __future__ import annotations

import unittest

from trading_system.market_session_profile import (
    MarketSessionProfile,
    resolve_market_session_profile,
)


class MarketSessionProfileTests(unittest.TestCase):
    def test_returns_only_exact_registered_broker_market(self) -> None:
        profile = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="OBSERVED_CALENDAR",
        )

        resolved = resolve_market_session_profile(
            "Observed Market Label",
            profiles=(profile,),
        )

        self.assertIs(resolved, profile)

    def test_unknown_market_fails_closed_without_alias_or_fallback(self) -> None:
        profile = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="OBSERVED_CALENDAR",
        )

        with self.assertRaisesRegex(ValueError, "unsupported eToro market"):
            resolve_market_session_profile("observed market label", profiles=(profile,))

    def test_duplicate_registered_market_is_ambiguous_and_fails_closed(self) -> None:
        first = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="CALENDAR_A",
        )
        second = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="CALENDAR_B",
        )

        with self.assertRaisesRegex(ValueError, "ambiguous eToro market profile"):
            resolve_market_session_profile(
                "Observed Market Label",
                profiles=(first, second),
            )

    def test_profile_rejects_invalid_timezone(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid IANA timezone"):
            MarketSessionProfile(
                etoro_market="Observed Market Label",
                market_timezone="Not/A_Timezone",
                calendar_id="OBSERVED_CALENDAR",
            )

    def test_profile_and_lookup_require_trimmed_nonblank_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "etoro_market"):
            MarketSessionProfile(
                etoro_market=" Observed Market Label ",
                market_timezone="Europe/London",
                calendar_id="OBSERVED_CALENDAR",
            )

        with self.assertRaisesRegex(ValueError, "etoro_market"):
            resolve_market_session_profile(" ", profiles=())


if __name__ == "__main__":
    unittest.main()
