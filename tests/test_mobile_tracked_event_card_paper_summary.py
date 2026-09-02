from __future__ import annotations

import unittest
from pathlib import Path


CARD_PATH = Path("mobile/src/components/TrackedEventsSection.tsx")
PAPER_SUMMARY_PATH = Path("mobile/src/components/TrackedEventPaperSummary.tsx")


class MobileTrackedEventCardPaperSummaryTests(unittest.TestCase):
    def test_earnings_card_always_renders_paper_summary_independent_of_release_action(self) -> None:
        card = CARD_PATH.read_text(encoding="utf-8")

        self.assertIn("event.kind === 'earnings'", card)
        self.assertIn("<TrackedEventPaperSummary", card)
        self.assertIn("eventId={event.event_id}", card)
        self.assertIn("expectationEventId={expectationCandidateId}", card)

        summary_start = card.index("<TrackedEventPaperSummary")
        summary_end = card.index("/>", summary_start)
        summary = card[summary_start:summary_end]
        self.assertNotIn("action_required", summary)
        self.assertNotIn("action_target", summary)

    def test_paper_summary_reads_permission_and_canonical_paper_status(self) -> None:
        source = PAPER_SUMMARY_PATH.read_text(encoding="utf-8")

        self.assertIn("getTrackedEventPaperPermission(eventId)", source)
        self.assertIn("getPaperStatus(expectationEventId)", source)
        self.assertIn("PAPER-lupa ja asetukset", source)
        self.assertIn("pathname: '/tracked-events/[eventId]/release'", source)
        self.assertIn("params: { eventId }", source)

    def test_paper_summary_exposes_permission_and_execution_lifecycle(self) -> None:
        source = PAPER_SUMMARY_PATH.read_text(encoding="utf-8")

        self.assertIn("Lupa hyväksytty", source)
        self.assertIn("Lupa vanhentunut", source)
        self.assertIn("Lupa puuttuu", source)
        self.assertIn("Kauppa: ei vielä käsitelty.", source)
        self.assertIn("Kauppa: odottaa vahvistuksia", source)
        self.assertIn("Kauppa: PAPER-kauppa toteutettu", source)
        self.assertIn("Kauppa: NO TRADE", source)

    def test_execution_status_is_bound_to_current_expectation_version(self) -> None:
        source = PAPER_SUMMARY_PATH.read_text(encoding="utf-8")

        self.assertIn("run.expectation_version !== status.expectation_version", source)
        self.assertIn("Kaupan aiempi tila on expectation v", source)
        self.assertIn("nykyinen v", source)

    def test_latest_reaction_percent_is_formatted_to_two_decimals(self) -> None:
        card = CARD_PATH.read_text(encoding="utf-8")

        self.assertIn("formatReactionPercent(reaction.return_pct)", card)
        self.assertIn("parsed.toFixed(2)", card)
        self.assertNotIn("Muutos {reaction.return_pct} %", card)


if __name__ == "__main__":
    unittest.main()
