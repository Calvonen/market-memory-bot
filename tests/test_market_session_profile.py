from __future__ import annotations

import unittest

from trading_system.market_session_profile import (
    GROUNDED_MARKET_SESSION_PROFILES,
    SYDNEY_MARKET_SESSION_PROFILE,
    MarketSessionProfile,
    has_grounded_market_session_profile,
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

    def test_grounded_sydney_profile_uses_asx_timezone_and_mic(self) -> None:
        self.assertEqual(SYDNEY_MARKET_SESSION_PROFILE.etoro_market, "Sydney")
        self.assertEqual(SYDNEY_MARKET_SESSION_PROFILE.market_timezone, "Australia/Sydney")
        self.assertEqual(SYDNEY_MARKET_SESSION_PROFILE.calendar_id, "XASX")
        self.assertEqual(GROUNDED_MARKET_SESSION_PROFILES, (SYDNEY_MARKET_SESSION_PROFILE,))

        resolved = resolve_market_session_profile(
            "Sydney",
            profiles=GROUNDED_MARKET_SESSION_PROFILES,
        )
        self.assertIs(resolved, SYDNEY_MARKET_SESSION_PROFILE)

    def test_grounded_profiles_do_not_alias_or_infer_other_australian_labels(self) -> None:
        for label in ("sydney", "Australia", "ASX", "XASX"):
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "unsupported eToro market"):
                    resolve_market_session_profile(
                        label,
                        profiles=GROUNDED_MARKET_SESSION_PROFILES,
                    )

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


class HasGroundedMarketSessionProfileTests(unittest.TestCase):
    def test_grounded_sydney_is_the_only_currently_rolled_out_market(self) -> None:
        self.assertTrue(has_grounded_market_session_profile("Sydney"))

    def test_markets_awaiting_grounding_report_false_instead_of_raising(self) -> None:
        # London/NASDAQ tracked events already run the existing reaction
        # monitor; they simply have no grounded session profile yet, which the
        # predicate must report as "not rolled out" rather than as an error.
        for label in ("London", "NASDAQ", "NYSE", "Helsinki"):
            with self.subTest(label=label):
                self.assertFalse(has_grounded_market_session_profile(label))

    def test_never_aliases_or_infers_from_ticker_country_or_calendar_market(self) -> None:
        for label in ("sydney", "SYDNEY", " Sydney", "Sydney ", "Australia", "ASX", "XASX"):
            with self.subTest(label=label):
                self.assertFalse(has_grounded_market_session_profile(label))

    def test_missing_or_blank_market_is_not_grounded(self) -> None:
        for label in (None, "", " "):
            with self.subTest(label=label):
                self.assertFalse(has_grounded_market_session_profile(label))

    def test_ambiguously_registered_label_is_not_grounded(self) -> None:
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

        self.assertFalse(
            has_grounded_market_session_profile(
                "Observed Market Label", profiles=(first, second)
            )
        )

    def test_agrees_exactly_with_resolve_market_session_profile(self) -> None:
        profile = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="OBSERVED_CALENDAR",
        )
        profiles = (profile,)

        for label in ("Observed Market Label", "observed market label", "Other", "", " "):
            with self.subTest(label=label):
                try:
                    resolve_market_session_profile(label, profiles=profiles)
                except ValueError:
                    resolvable = False
                else:
                    resolvable = True
                self.assertEqual(
                    has_grounded_market_session_profile(label, profiles=profiles), resolvable
                )


if __name__ == "__main__":
    unittest.main()
