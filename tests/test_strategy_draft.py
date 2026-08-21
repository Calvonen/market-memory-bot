import unittest
from datetime import date

from pydantic import ValidationError

from trading_system.models import EventExpectation
from trading_system.strategy_draft import (
    StrategyDraftPayload,
    changed_fields,
    draft_fingerprint,
    draft_warnings,
    normalize_draft,
)


def _payload(**overrides) -> StrategyDraftPayload:
    defaults = dict(
        instrument="HAS.L",
        event_name="Hays plc FY2026 results",
        scheduled_date=date(2026, 8, 20),
        consensus={"fy27_operating_profit_pre_exceptional_gbp_m": 55.6},
        important_kpis=["fy27_operating_profit_pre_exceptional_gbp_m"],
        bull_case=["Fees grow above consensus"],
        base_case=["In-line results"],
        bear_case=["Margin compression"],
        triggers={"bull_fy27_operating_profit_gbp_m": 60.0},
        invalidation_conditions=["Guidance withdrawn"],
        source_name="Hays plc analyst consensus",
        source_url="https://example.com/consensus",
        source_as_of=date(2026, 7, 1),
        change_note="initial draft",
        summary="Hays FY26 results strategy",
        assumptions=["Fee income trend continues"],
        unresolved_questions=[],
    )
    defaults.update(overrides)
    return StrategyDraftPayload(**defaults)


def _expectation(**overrides) -> EventExpectation:
    defaults = dict(
        event_id="hays-fy2026-results",
        instrument="HAS.L",
        event_name="Hays plc FY2026 results",
        scheduled_date=date(2026, 8, 20),
        consensus={"fy27_operating_profit_pre_exceptional_gbp_m": 55.6},
        important_kpis=("fy27_operating_profit_pre_exceptional_gbp_m",),
        bull_case=("Fees grow above consensus",),
        base_case=("In-line results",),
        bear_case=("Margin compression",),
        triggers={"bull_fy27_operating_profit_gbp_m": 60.0},
        invalidation_conditions=("Guidance withdrawn",),
        source_name="Hays plc analyst consensus",
        source_url="https://example.com/consensus",
        source_as_of=date(2026, 7, 1),
        version=1,
    )
    defaults.update(overrides)
    return EventExpectation(**defaults)


class StrategyDraftPayloadValidationTests(unittest.TestCase):
    def test_whitespace_only_change_note_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            _payload(change_note="   ")

    def test_whitespace_only_summary_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            _payload(summary="\t\n  ")

    def test_change_note_just_under_min_length_after_stripping_is_rejected(self) -> None:
        # "ab" (2 chars) padded to whitespace-length 3 would pass a naive
        # min_length=3 check before stripping; it must still fail once
        # stripped, since the real content is only 2 characters.
        with self.assertRaises(ValidationError):
            _payload(change_note=" ab ")

    def test_change_note_and_summary_are_stripped_after_validation(self) -> None:
        payload = _payload(change_note="  raise bull threshold  ", summary="  Hays FY26 strategy  ")

        self.assertEqual(payload.change_note, "raise bull threshold")
        self.assertEqual(payload.summary, "Hays FY26 strategy")


class NormalizeDraftTests(unittest.TestCase):
    def test_strips_whitespace_and_dedupes_lists(self) -> None:
        payload = _payload(
            bull_case=["  Fees grow  ", "Fees grow", "New driver"],
            important_kpis=[" fy27_operating_profit_pre_exceptional_gbp_m "],
        )
        normalized = normalize_draft("hays-fy2026-results", payload)

        self.assertEqual(normalized["bull_case"], ["Fees grow", "New driver"])
        self.assertEqual(
            normalized["important_kpis"], ["fy27_operating_profit_pre_exceptional_gbp_m"]
        )

    def test_drops_empty_source_strings_to_none(self) -> None:
        payload = _payload(source_name="   ", source_url=None)
        normalized = normalize_draft("hays-fy2026-results", payload)

        self.assertIsNone(normalized["source_name"])
        self.assertIsNone(normalized["source_url"])

    def test_consensus_null_value_round_trips_as_real_none(self) -> None:
        payload = _payload(
            consensus={
                "fy27_operating_profit_pre_exceptional_gbp_m": None,
                "other_metric_gbp_m": 55.6,
            }
        )
        normalized = normalize_draft("hays-fy2026-results", payload)

        self.assertIsNone(
            normalized["consensus"]["fy27_operating_profit_pre_exceptional_gbp_m"]
        )
        # Never the *string* "null" - a real None, distinguishable in a type
        # check (isinstance check would fail for a stray string).
        self.assertNotEqual(
            normalized["consensus"]["fy27_operating_profit_pre_exceptional_gbp_m"], "null"
        )
        self.assertEqual(normalized["consensus"]["other_metric_gbp_m"], 55.6)


class DraftFingerprintTests(unittest.TestCase):
    def test_identical_drafts_produce_identical_fingerprints(self) -> None:
        normalized_a = normalize_draft("hays-fy2026-results", _payload())
        normalized_b = normalize_draft("hays-fy2026-results", _payload())

        self.assertEqual(draft_fingerprint(normalized_a), draft_fingerprint(normalized_b))

    def test_any_field_change_changes_the_fingerprint(self) -> None:
        base = normalize_draft("hays-fy2026-results", _payload())
        changed = normalize_draft(
            "hays-fy2026-results", _payload(summary="A materially different summary")
        )

        self.assertNotEqual(draft_fingerprint(base), draft_fingerprint(changed))

    def test_list_order_changes_the_fingerprint(self) -> None:
        # Scenario ordering is meaningful narrative content, not an
        # unordered set - reordering bull_case must count as a real change.
        base = normalize_draft(
            "hays-fy2026-results", _payload(bull_case=["First driver", "Second driver"])
        )
        reordered = normalize_draft(
            "hays-fy2026-results", _payload(bull_case=["Second driver", "First driver"])
        )

        self.assertNotEqual(draft_fingerprint(base), draft_fingerprint(reordered))


class DraftWarningsTests(unittest.TestCase):
    def test_clean_draft_against_matching_current_has_no_conflict_warnings(self) -> None:
        current = _expectation()
        normalized = normalize_draft("hays-fy2026-results", _payload())

        warnings = draft_warnings(normalized, current)

        self.assertEqual(warnings, [])

    def test_warns_on_kpi_not_present_in_consensus(self) -> None:
        payload = _payload(important_kpis=["unknown_metric_gbp_m"])
        normalized = normalize_draft("hays-fy2026-results", payload)

        warnings = draft_warnings(normalized, _expectation())

        self.assertTrue(any("unknown_metric_gbp_m" in w for w in warnings))

    def test_warns_on_trigger_without_matching_consensus_metric(self) -> None:
        payload = _payload(triggers={"bull_unrelated_metric_gbp_m": 10.0})
        normalized = normalize_draft("hays-fy2026-results", payload)

        warnings = draft_warnings(normalized, _expectation())

        self.assertTrue(any("bull_unrelated_metric_gbp_m" in w for w in warnings))

    def test_warns_on_empty_scenarios_and_missing_invalidation_conditions(self) -> None:
        payload = _payload(bull_case=[], base_case=[], bear_case=[], invalidation_conditions=[])
        normalized = normalize_draft("hays-fy2026-results", payload)

        warnings = draft_warnings(normalized, _expectation())

        self.assertIn("bull-skenaario on tyhjä", warnings)
        self.assertIn("base-skenaario on tyhjä", warnings)
        self.assertIn("bear-skenaario on tyhjä", warnings)
        self.assertIn("mitätöintiehtoja (NO TRADE -ehtoja) ei ole määritelty", warnings)

    def test_warns_on_missing_source(self) -> None:
        payload = _payload(source_name=None, source_url=None)
        normalized = normalize_draft("hays-fy2026-results", payload)

        warnings = draft_warnings(normalized, _expectation())

        self.assertIn("lähdettä ei ole merkitty", warnings)

    def test_warns_on_unresolved_questions(self) -> None:
        payload = _payload(unresolved_questions=["Will guidance be reaffirmed?"])
        normalized = normalize_draft("hays-fy2026-results", payload)

        warnings = draft_warnings(normalized, _expectation())

        self.assertIn("1 ratkaisematonta kysymystä ennen hyväksyntää", warnings)

    def test_warns_on_instrument_or_date_drift_from_current(self) -> None:
        payload = _payload(instrument="DIFFERENT.L", scheduled_date=date(2026, 9, 1))
        normalized = normalize_draft("hays-fy2026-results", payload)

        warnings = draft_warnings(normalized, _expectation())

        self.assertTrue(any("instrument" in w for w in warnings))
        self.assertTrue(any("scheduled_date" in w for w in warnings))

    def test_no_current_expectation_skips_drift_warnings_but_still_checks_completeness(self) -> None:
        normalized = normalize_draft("new-event", _payload())

        warnings = draft_warnings(normalized, None)

        self.assertEqual(warnings, [])


class ChangedFieldsTests(unittest.TestCase):
    def test_no_changes_yields_empty_diff(self) -> None:
        normalized = normalize_draft("hays-fy2026-results", _payload())

        self.assertEqual(changed_fields(normalized, _expectation()), [])

    def test_changed_field_is_reported(self) -> None:
        payload = _payload(triggers={"bull_fy27_operating_profit_gbp_m": 62.0})
        normalized = normalize_draft("hays-fy2026-results", payload)

        self.assertEqual(changed_fields(normalized, _expectation()), ["triggers"])

    def test_no_current_expectation_reports_every_field_as_changed(self) -> None:
        normalized = normalize_draft("new-event", _payload())

        diff = changed_fields(normalized, None)

        self.assertIn("instrument", diff)
        self.assertIn("bull_case", diff)


if __name__ == "__main__":
    unittest.main()
