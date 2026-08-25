from __future__ import annotations

import unittest

from trading_system.market_session_profile import (
    GROUNDED_MARKET_SESSION_PROFILES,
    SYDNEY_MARKET_SESSION_PROFILE,
    MarketSessionProfile,
    has_grounded_market_session_profile,
    resolve_market_session_profile,
    resolve_provider_symbol,
)


class MarketSessionProfileTests(unittest.TestCase):
    def test_returns_only_exact_registered_broker_market(self) -> None:
        profile = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="OBSERVED_CALENDAR",
            broker_symbol_suffix=".OBS",
            provider_symbol_suffix=".OB",
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
            broker_symbol_suffix=".OBS",
            provider_symbol_suffix=".OB",
        )

        with self.assertRaisesRegex(ValueError, "unsupported eToro market"):
            resolve_market_session_profile("observed market label", profiles=(profile,))

    def test_duplicate_registered_market_is_ambiguous_and_fails_closed(self) -> None:
        first = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="CALENDAR_A",
            broker_symbol_suffix=".OBS",
            provider_symbol_suffix=".OB",
        )
        second = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="CALENDAR_B",
            broker_symbol_suffix=".OBS",
            provider_symbol_suffix=".OB",
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
                broker_symbol_suffix=".OBS",
                provider_symbol_suffix=".OB",
            )

    def test_profile_and_lookup_require_trimmed_nonblank_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "etoro_market"):
            MarketSessionProfile(
                etoro_market=" Observed Market Label ",
                market_timezone="Europe/London",
                calendar_id="OBSERVED_CALENDAR",
                broker_symbol_suffix=".OBS",
                provider_symbol_suffix=".OB",
            )

        with self.assertRaisesRegex(ValueError, "etoro_market"):
            resolve_market_session_profile(" ", profiles=())


class ResolveProviderSymbolTests(unittest.TestCase):
    def test_sydney_translates_the_asx_broker_suffix_to_the_yahoo_one(self) -> None:
        self.assertEqual(SYDNEY_MARKET_SESSION_PROFILE.broker_symbol_suffix, ".ASX")
        self.assertEqual(SYDNEY_MARKET_SESSION_PROFILE.provider_symbol_suffix, ".AX")
        self.assertEqual(
            resolve_provider_symbol("WDS.ASX", profile=SYDNEY_MARKET_SESSION_PROFILE),
            "WDS.AX",
        )

    def test_policy_covers_every_sydney_instrument_not_just_the_first_one(self) -> None:
        for broker, provider in (
            ("WDS.ASX", "WDS.AX"),
            ("NHF.ASX", "NHF.AX"),
            ("BHP.ASX", "BHP.AX"),
            ("A2M.ASX", "A2M.AX"),
        ):
            with self.subTest(broker=broker):
                self.assertEqual(
                    resolve_provider_symbol(broker, profile=SYDNEY_MARKET_SESSION_PROFILE),
                    provider,
                )

    def test_symbol_without_the_declared_broker_suffix_fails_closed(self) -> None:
        for broker in ("WDS", "WDS.AX", "WDS.L", "WDS.NASDAQ", "WDS."):
            with self.subTest(broker=broker):
                with self.assertRaises(ValueError):
                    resolve_provider_symbol(broker, profile=SYDNEY_MARKET_SESSION_PROFILE)

    def test_never_case_folds_the_broker_symbol(self) -> None:
        for broker in ("wds.asx", "WDS.asx", "Wds.Asx"):
            with self.subTest(broker=broker):
                with self.assertRaises(ValueError):
                    resolve_provider_symbol(broker, profile=SYDNEY_MARKET_SESSION_PROFILE)

    def test_blank_untrimmed_or_baseless_symbols_fail_closed(self) -> None:
        for broker in ("", "   ", " WDS.ASX", "WDS.ASX ", ".ASX", "A.B.ASX"):
            with self.subTest(broker=broker):
                with self.assertRaises(ValueError):
                    resolve_provider_symbol(broker, profile=SYDNEY_MARKET_SESSION_PROFILE)

    def test_translation_is_driven_only_by_the_supplied_profile(self) -> None:
        other = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="XASX",
            broker_symbol_suffix=".ASX",
            provider_symbol_suffix=".OTHER",
        )

        self.assertEqual(resolve_provider_symbol("WDS.ASX", profile=other), "WDS.OTHER")

    def test_profile_rejects_a_malformed_symbol_policy(self) -> None:
        for broker_suffix, provider_suffix in (
            ("", ".AX"),
            (".ASX", ""),
            ("ASX", ".AX"),
            (".ASX", "AX"),
            (" .ASX", ".AX"),
            (".asx", ".AX"),
            (".ASX", ".ax"),
        ):
            with self.subTest(broker=broker_suffix, provider=provider_suffix):
                with self.assertRaises(ValueError):
                    MarketSessionProfile(
                        etoro_market="Observed Market Label",
                        market_timezone="Europe/London",
                        calendar_id="OBSERVED_CALENDAR",
                        broker_symbol_suffix=broker_suffix,
                        provider_symbol_suffix=provider_suffix,
                    )


class HasGroundedMarketSessionProfileTests(unittest.TestCase):
    def test_grounded_sydney_is_the_only_currently_rolled_out_market(self) -> None:
        self.assertTrue(has_grounded_market_session_profile("Sydney"))

    def test_markets_awaiting_grounding_report_false_instead_of_raising(self) -> None:
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

    def test_ambiguously_registered_label_fails_closed(self) -> None:
        first = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="CALENDAR_A",
            broker_symbol_suffix=".OBS",
            provider_symbol_suffix=".OB",
        )
        second = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="CALENDAR_B",
            broker_symbol_suffix=".OBS",
            provider_symbol_suffix=".OB",
        )

        with self.assertRaisesRegex(ValueError, "ambiguous eToro market profile"):
            has_grounded_market_session_profile(
                "Observed Market Label", profiles=(first, second)
            )

    def test_agrees_with_resolver_for_supported_and_unsupported_labels(self) -> None:
        profile = MarketSessionProfile(
            etoro_market="Observed Market Label",
            market_timezone="Europe/London",
            calendar_id="OBSERVED_CALENDAR",
            broker_symbol_suffix=".OBS",
            provider_symbol_suffix=".OB",
        )
        profiles = (profile,)

        self.assertTrue(
            has_grounded_market_session_profile("Observed Market Label", profiles=profiles)
        )
        for label in ("observed market label", "Other", "", " "):
            with self.subTest(label=label):
                self.assertFalse(has_grounded_market_session_profile(label, profiles=profiles))


if __name__ == "__main__":
    unittest.main()
